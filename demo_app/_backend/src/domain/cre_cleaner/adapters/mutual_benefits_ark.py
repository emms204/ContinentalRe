"""Mutual Benefits via ARK — first-pass, unverified.

Layout is closer to AIICO ARK: per-class / per-layer premium .xls files plus
separate claims workbooks. Prefer monthly discovery when month names appear;
otherwise fall back to quarterly files.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

from src.domain.cre_cleaner.adapters.monthly_files import MonthlyPremiumAdapter
from src.domain.cre_cleaner.adapters.base import (
    EXCEL_SUFFIXES,
    list_input_files,
    name_tokens,
    quarters_in_text,
    years_in_text,
)


# Loss bordereaux (2020–2025, ARK and Jomola Agric): CLAIM NO | INSURED /
# PAYEE | POLICY NO | (COVER FROM | COVER TO) | GROSS AMOUNT or AMOUNT PAID |
# TREATY | MBA NET (Mutual Benefits' own retention). Outstanding 2020:
# AMOUNT OF LOSS | TREATY SHARE | NET.
MUTUAL_CLAIMS_ALIAS_EXTRA = {
    "insured": ["PAYEE"],
    "total_claims": ["GROSS AMOUNT", "AMOUNT PAID", "AMOUNT OF LOSS"],
    "amount_ret": ["MBA NET", "NET"],
    "period_from": ["POLICY COVER FROM"],
    "period_to": ["POLICY COVER TO"],
}


_CLASS_ROW = re.compile(r"^\s*CLASS\s*:?\s*$", re.IGNORECASE)


def _is_report_tab(sheet: str) -> bool:
    """'rein_treaty' / 'REIN TREATY': the reinsurance report's tab name."""
    words = [w for w in re.split(r"[^A-Z0-9]+", (sheet or "").upper()) if w]
    return bool(words) and set(words) <= {"REIN", "TREATY"} and "REIN" in words


class MutualBenefitsArkAdapter(MonthlyPremiumAdapter):
    """Discovery plus Mutual's loss-bordereau column names; band / class /
    tab rules are the generic ones (IMPL-20260929-04: no longer inherits the
    AIICO adapter's rules)."""

    cedant = "MUTUAL BENEFITS"
    broker = "ARK"
    verified = False
    status_note = (
        "Mutual Benefits & ARK: month-file discovery + quarterly fallback; "
        "not checked against Bisola gold"
    )
    # Kept from the pre-IMPL-04 behaviour (inherited then).
    content_sheet_typing = True
    # Marine tabs carry a pasted 'CLASS OF BUSINESS: ENGINEERING' banner; the
    # tab name is the class (gold: MARINE HULL / MARINE CARGO - CLAIMS).
    claims_sheet_class_first = True
    # Gold fills claims PPN from the amounts (flagged Calculated); the source
    # has amounts only.
    settings = {"claims_ppn_calculated": True}
    claims_alias_extra = MUTUAL_CLAIMS_ALIAS_EXTRA
    # WEF / WET: with effect from / to (2023 Q3 class bordereaux).
    premium_alias_extra = {"period_from": ["WEF"], "period_to": ["WET"]}
    # Treaty Premium Bordereau exports (2020) have one 'rein_treaty' tab and a
    # 'CLASS: | | MARINE HULL INSURANCE' row grouping the rows below it. The
    # class in the sheet wins over the tab name, so scan it on every tab.
    premium_section_class_first = True

    def sheet_class_hint(self, sheet, class_hint):
        # 'rein_treaty' is the export's report name, never a class.
        return "" if _is_report_tab(sheet) else class_hint

    def premium_banner_label(self, cells, text):
        """A 'CLASS: | | CONTRACTOR ALL RISK' row always starts a new group.
        Mutual names subclasses there (PROFESSIONAL INDEMNITY, HOUSEHOLDER
        INSURANCE): use the class map's parent, else keep the source text so
        the rows go to class review instead of inheriting the previous group."""
        if not (len(cells) == 2 and _CLASS_ROW.match(cells[0])):
            return None
        from src.domain.cre_cleaner.core.class_labels import banner_class_label, normalize_class_label

        mapped = normalize_class_label(text, self.class_map())
        return banner_class_label(text) or (mapped if mapped not in {"", "Other"} else text)

    def resolve_premium_class(self, *, sheet_hint, section, raw, **context):
        """In-sheet class (CLASS: section row, then a row class column) before
        the tab name; the tab name only when the sheet states no class."""
        from src.domain.cre_cleaner.core.class_labels import is_unresolved_class, normalize_class_label

        if section and not is_unresolved_class(section):
            return section, "banner", section
        mapped = normalize_class_label(raw, self.class_map())
        if mapped not in {"", "Other"} and not is_unresolved_class(mapped):
            return mapped, "column", raw
        if sheet_hint and not is_unresolved_class(sheet_hint):
            return sheet_hint, "sheet", context.get("sheet", sheet_hint)
        return section or raw or sheet_hint, "banner" if section else ("column" if raw else "sheet"), raw or section or sheet_hint

    def discover_quarterly_premium_files(self, raw_dir: Path, year: int, quarter: int) -> List[Path]:
        """Class/layer premium files without a month in the name, for the quarter."""
        out = []
        for p in list_input_files(raw_dir, year):
            if p.suffix.lower() not in EXCEL_SUFFIXES:
                continue
            toks = name_tokens(p.stem)
            if any(t in self._NON_PREMIUM_TOKENS for t in toks):
                # Claims / loss files stay out of premium
                if any(t.startswith("PREM") for t in toks):
                    pass
                else:
                    continue
            # Month-named files belong to monthly discovery
            from src.domain.cre_cleaner.adapters.base import token_month
            if any(token_month(t) for t in toks):
                continue
            label = p.name
            qs = quarters_in_text(label) or quarters_in_text(str(p.parent))
            if qs and quarter not in qs:
                continue
            years = years_in_text(p.name)
            if years and year not in years:
                continue
            # Prefer files that look like premium / surplus / quota bordereaux
            if not any(
                t.startswith("PREM") or t in {"SURPLUS", "SURP", "QUOTA", "FIRE", "BOND",
                                              "ENGINEERING", "MARINE", "CARGO", "HULL",
                                              "GENERAL", "ACCIDENT"}
                for t in toks
            ):
                continue
            out.append(p)
        return out

    def discover_claims_files(self, raw_dir: Path, year: int, quarter: int) -> List[Path]:
        # CLAIM + quarter patterns; also Mutual's
        # "Treaty Paid Claim Recovery" / "LOSS BORDERAUX" naming.
        q_patterns = {
            1: ["Q1", "1ST", "FIRST"],
            2: ["Q2", "2ND", "SECOND"],
            3: ["Q3", "3RD", "THIRD"],
            4: ["Q4", "4TH", "FOURTH"],
        }[quarter]
        out = []
        for p in list_input_files(raw_dir, year):
            if p.suffix.lower() not in EXCEL_SUFFIXES:
                continue
            u = p.name.upper()
            if not any(t in u for t in ("CLAIM", "LOSS", "RECOVERY", "OUTSTANDING")):
                continue
            if not any(tok in u for tok in q_patterns):
                continue
            years = years_in_text(p.name)
            if years and year not in years:
                continue
            out.append(p)
        return out


# Keep a thin alias used by the registry name
MutualBenefitsAdapter = MutualBenefitsArkAdapter
