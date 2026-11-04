"""Mutual Benefits adapters (ARK, JOMOLA AGRIC) — first-pass, unverified.

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
    QuarterlyWorkbookAdapter,
    list_input_files,
    name_tokens,
    quarters_in_text,
    years_in_text,
)
from src.domain.cre_cleaner.core.normalize import normalize_header


# Loss bordereaux (2020–2025, ARK and Jomola Agric): CLAIM NO | INSURED /
# PAYEE | POLICY NO | (COVER FROM | COVER TO) | GROSS AMOUNT or AMOUNT PAID |
# TREATY | MBA NET (Mutual Benefits' own retention). Outstanding 2020:
# AMOUNT OF LOSS | TREATY SHARE | NET. Q4 2021 per-class tabs: Assured Name |
# Total Paid | RETENTION | % | (TREATY/POOL) AMOUNT | %.
MUTUAL_CLAIMS_ALIAS_EXTRA = {
    "insured": ["PAYEE", "ASSURED NAME"],
    "total_claims": ["GROSS AMOUNT", "AMOUNT PAID", "AMOUNT OF LOSS", "TOTAL PAID"],
    "amount_ret": ["MBA NET", "NET", "RETENTION"],
    "period_from": ["POLICY COVER FROM"],
    "period_to": ["POLICY COVER TO"],
}


_CLASS_ROW = re.compile(r"^\s*CLASS\s*:?\s*$", re.IGNORECASE)

# 'rein_treaty' premium exports split the header over two rows: identity
# labels and '<--Band-->' labels on the first, Sum Insured / % / Premium under
# each band on the second. The identity labels name the blank cells below them.
# Keys are normalize_header forms ('Policy No.' / 'POLICY  NO' -> 'POLICY NO').
_REIN_IDENTITY = {"POLICY NO": "POLICY NO", "INSURED": "INSURED"}
_REIN_PERIOD = "INS PERIOD"
# Besides INSURED, one of these marks the band row (2023 ENGINEERING 2ND
# QUARTER has no policy label: its policy numbers sit under a blank heading).
_REIN_ROW_MARKERS = {"CESS", "INS PERIOD", "POLICY NO"}
_REIN_BAND_ARROW = re.compile(r"^<-+\s*(.*?)\s*-*>$")
# Band label word -> upload band (None: the gross block).
_REIN_BANDS = (("GROSS", None), ("RETEN", "ret"), ("SURPLUS", "sur"), ("QUOTA", "sur"),
               ("TREATY", "sur"), ("FACULT", "fac"))


def _rein_band_spans(group_row) -> List[Tuple[int, int, str]]:
    """(start, end, band) per '<--…-->' label in a rein_treaty band row; end is
    the next label's column (or None for the last band)."""
    starts = []
    for i, cell in enumerate(group_row or []):
        m = _REIN_BAND_ARROW.match(str(cell or "").strip())
        if not m:
            continue
        label = m.group(1).upper()
        band = next((b for key, b in _REIN_BANDS if key in label), "?")
        starts.append((i, band))
    return [(start, starts[n + 1][0] if n + 1 < len(starts) else None, band)
            for n, (start, band) in enumerate(starts)]


def _is_rein_band_row(group_row) -> bool:
    """The Mutual two-row premium header: INSURED plus Cess. / Ins. Period /
    Policy No. on the band row, and at least two band arrows (gross plus one
    allocation band)."""
    labels = {normalize_header(c) for c in group_row or []}
    return ("INSURED" in labels and bool(labels & _REIN_ROW_MARKERS)
            and len(_rein_band_spans(group_row)) >= 2)


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

    def premium_header_cells(self, raw_rows, header_at, header, group_row):
        """rein_treaty: Policy No. / Insured / Ins. Period sit on the band row,
        above blank cells of the Sum Insured row. Other layouts unchanged."""
        if not _is_rein_band_row(group_row):
            return list(header), group_row
        cells = list(header) + [None] * max(0, len(group_row) - len(header))
        for i, label in enumerate(group_row):
            key = normalize_header(label)
            if str(cells[i] or "").strip():
                continue
            if key in _REIN_IDENTITY:
                cells[i] = _REIN_IDENTITY[key]
            elif key == _REIN_PERIOD and i + 1 < len(cells) and not str(cells[i + 1] or "").strip():
                cells[i], cells[i + 1] = "FROM", "TO"
        return cells, group_row

    def map_premium_columns(self, header, group_row, *, path=None, sheet="", exceptions=None):
        """rein_treaty: each band's Sum Insured / % / Premium are the cells under
        its own '<--Band-->' label. The generic pairing gives a band's % the
        next band's Sum Insured. Other layouts unchanged."""
        cmap = super().map_premium_columns(header, group_row, path=path, sheet=sheet,
                                           exceptions=exceptions)
        if not _is_rein_band_row(group_row):
            return cmap
        names = [str(h or "").strip().upper() for h in header]
        assigned = set()
        own_cols = set()
        extra, ignored = [], []
        for start, end, band in _rein_band_spans(group_row):
            span = range(start, len(names) if end is None else end)
            cols = {key: next((j for j in span if names[j] == label), None)
                    for key, label in (("si", "SUM INSURED"), ("ppn", "%"), ("prem", "PREMIUM"))}
            label = str(group_row[start]).strip("<-> ").strip()
            if band is None:
                if cols["si"] is not None:
                    cmap.mapping["sum_insured"] = cols["si"]
                if cols["prem"] is not None:
                    cmap.mapping["gross_premium"] = cols["prem"]
            elif band == "?":
                continue
            elif band not in assigned:
                assigned.add(band)
                for key, col in cols.items():
                    setattr(cmap, f"{band}_{key}", col)
            elif band == "sur":
                # A further treaty layer under its own label: kept as a layer.
                extra.append((label.upper(), cols["ppn"], cols["si"], cols["prem"]))
            else:
                ignored.append(f"{label.upper()} (cols {start + 1}-{(end or len(names))})")
            own_cols.update(c for c in cols.values() if c is not None)
        # The band columns are now each band's own: drop generic extra layers and
        # borrowed-column notes about them, keep anything else.
        cmap.extra_treaty_blocks = [b for b in cmap.extra_treaty_blocks
                                    if not own_cols & {c for c in b[1:] if c is not None}] + extra
        cmap.ignored_blocks = list(cmap.ignored_blocks) + ignored
        resolved = tuple([f"{name} " for band, name in (("sur", "TREATY"), ("fac", "FAC"))
                          if band in assigned] + [f"{layer[0]} " for layer in extra])
        cmap.borrowed_blocks = [b for b in cmap.borrowed_blocks
                                if not (resolved and b.startswith(resolved)
                                        and not b.startswith("TREATY LAYER"))]
        return cmap

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


class MutualBenefitsJomolaAgricAdapter(QuarterlyWorkbookAdapter):
    cedant = "MUTUAL BENEFITS"
    broker = "JOMOLA AGRIC"
    settings = {"single_class": "Agriculture"}
    claims_alias_extra = MUTUAL_CLAIMS_ALIAS_EXTRA
    verified = False
    status_note = "Mutual Benefits Jomola Agric: quarterly returns; unverified"
