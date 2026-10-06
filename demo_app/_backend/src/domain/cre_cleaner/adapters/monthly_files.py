"""Monthly premium-file discovery (cedant-neutral).

Cedants whose brokers send one premium workbook per month (``OCT 2024
loc.xlsx``, ``July 2021.xls``) plus quarterly / monthly claims workbooks.
Discovery only — no column, band, class or tab rules live here; those are
each adapter's own hooks (IMPL-20260929-04).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from src.domain.cre_cleaner.adapters.base import (
    EXCEL_SUFFIXES,
    BaseAdapter,
    list_input_files,
    name_tokens,
    quarters_in_text,
    token_month,
)
from src.domain.cre_cleaner.config import QUARTER_MONTHS, MONTH_NAMES


class MonthlyPremiumAdapter(BaseAdapter):
    """Month-named premium files + quarter/month-named claims files."""

    # Tokens that always mark a file as claims / outstanding — never monthly premium.
    # BORD / BORDEREAU / QTR alone are NOT here: "BORD JAN" and "JAN QTR PREM" are
    # monthly premium names. CLAIM/PAID/LOSS still exclude those files.
    _NON_PREMIUM_TOKENS = {
        "CLAIM", "CLAIMS", "OUTSTANDING", "OUT", "OST", "OS", "PAID",
        "LOSS", "LOSSES",
    }

    @staticmethod
    def _name_tokens(name: str) -> List[str]:
        return name_tokens(Path(name).stem)

    @classmethod
    def _token_month(cls, tok: str) -> Optional[int]:
        return token_month(tok)

    def classify_premium_candidate(self, name: str, year: int) -> Tuple[Optional[int], str]:
        """Return (month, reason). month is None when the file is not a monthly
        premium candidate; reason explains why (for the inventory / exceptions)."""
        if name.startswith("~$"):
            return None, "lock file"
        toks = self._name_tokens(name)
        if any(t in self._NON_PREMIUM_TOKENS for t in toks):
            return None, "claims/outstanding/quarterly file"
        # Quarter-only names without a month belong to quarterly discovery, not monthly.
        months = {m for m in (self._token_month(t) for t in toks) if m}
        if not months and quarters_in_text(name):
            return None, "quarterly file (no month)"
        years = {int(t) for t in toks if t.isdigit() and len(t) == 4 and 1990 <= int(t) <= 2100}
        if years and year not in years:
            return None, f"other year in name {sorted(years)}"
        if not months:
            return None, "no month in name"
        if len(months) > 1:
            return None, f"ambiguous: several months in name {sorted(months)}"
        return months.pop(), "ok"

    def _excel_files(self, raw_dir: Path, year: int) -> List[Path]:
        return [p for p in list_input_files(raw_dir, year) if p.suffix.lower() in EXCEL_SUFFIXES]

    def discover_premium_files(
        self, raw_dir: Path, year: int, quarter: int
    ) -> List[Tuple[int, Path]]:
        """Monthly premium files for the quarter, in calendar order.

        A file qualifies when its name carries exactly one month (full name,
        abbreviation or obvious misspelling) and no claims/outstanding/quarter
        marker, e.g. ``OCT 2024 loc.xlsx``, ``July 2021.xls``,
        ``DECEMBER 2024 PREM loc.xlsx``. When several files qualify for one
        month, a PREM/PREMIUM-named file is preferred and the choice is logged
        in ``self.discovery_notes``; if still ambiguous the best-scored file
        is used and a WARN note is raised (never read twice).
        """
        self.discovery_notes = []  # (severity, reason, filename, detail)
        months = QUARTER_MONTHS[quarter]
        by_month = {m: [] for m in months}
        for p in self._excel_files(raw_dir, year):
            m, _why = self.classify_premium_candidate(p.name, year)
            if m in by_month:
                by_month[m].append(p)
        found: List[Tuple[int, Path]] = []
        for month in months:
            cands = by_month[month]
            if not cands:
                continue
            if len(cands) == 1:
                found.append((month, cands[0]))
                continue

            def score(p: Path) -> tuple:
                toks = self._name_tokens(p.name)
                has_prem = any(t.startswith("PREM") for t in toks)
                has_year = str(year) in toks
                is_copy = bool(re.search(r"\(\d+\)", p.stem))
                return (has_prem, has_year, not is_copy)

            ranked = sorted(cands, key=lambda p: (tuple(not x for x in score(p)), p.name))
            chosen = ranked[0]
            others = [p.name for p in ranked[1:]]
            tie = score(ranked[0]) == score(ranked[1])
            self.discovery_notes.append((
                "WARN" if tie else "INFO",
                "premium_month_ambiguous" if tie else "premium_month_multiple_files",
                chosen.name,
                f"{MONTH_NAMES[month]}: {len(cands)} candidate files; using {chosen.name!r}, "
                f"not reading {others}" + (" (tie — please confirm controlling file)" if tie else
                                           " (PREM-named / year-named file preferred)"),
            ))
            found.append((month, chosen))
        return found

    def discover_claims_files(
        self, raw_dir: Path, year: int, quarter: int
    ) -> List[Path]:
        q_months = set(QUARTER_MONTHS[quarter])
        out = []
        for p in self._excel_files(raw_dir, year):
            u = p.name.upper()
            if "CLAIM" not in u and "LOSS" not in u and "OUTSTANDING" not in u:
                continue
            years = {int(t) for t in self._name_tokens(p.name)
                     if t.isdigit() and len(t) == 4 and 1990 <= int(t) <= 2100}
            if years and year not in years:
                continue
            # Quarterly claims bordereau. The quarter must be a whole token
            # (Q2, QTR 2, QTR. 2, 2ND QTR, SECOND QUARTER, ...) via
            # quarters_in_text; never a year digit ("Qtr 2021" is not Q2,
            # "2021 QTR" is not Q1) — IMPL-20260929-03.
            if quarter in quarters_in_text(p.name):
                out.append(p)
                continue
            # Monthly claims for months in this quarter (e.g. "JANUARY CLAIMS.xls")
            months = {m for m in (self._token_month(t) for t in self._name_tokens(p.name)) if m}
            if len(months) == 1 and months.pop() in q_months:
                out.append(p)
        # Prefer files with year in name when both exist; keep monthly + quarterly.
        out_year = [p for p in out if str(year) in p.name]
        return out_year or out
