"""Mutual Benefits via ARK — first-pass, unverified.

Layout is closer to AIICO ARK: per-class / per-layer premium .xls files plus
separate claims workbooks. Prefer monthly discovery when month names appear;
otherwise fall back to quarterly files.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from cre_cleaner.adapters.aiico_ark import AiicoArkAdapter
from cre_cleaner.adapters.base import (
    EXCEL_SUFFIXES,
    list_input_files,
    name_tokens,
    quarters_in_text,
    years_in_text,
)


class MutualBenefitsArkAdapter(AiicoArkAdapter):
    cedant = "MUTUAL BENEFITS"
    broker = "ARK"
    verified = False
    status_note = (
        "Mutual Benefits & ARK: AIICO-style month discovery + quarterly fallback; "
        "not checked against Bisola gold"
    )

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
            from cre_cleaner.adapters.base import token_month
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
        # Prefer AIICO-style CLAIM + quarter patterns; also Mutual's
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
