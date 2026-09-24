"""AIICO ARK-specific file discovery + column alias hints."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from cre_cleaner.adapters.base import BaseAdapter
from cre_cleaner.config import MONTH_ALIASES, QUARTER_MONTHS, MONTH_NAMES


class AiicoArkAdapter(BaseAdapter):
    cedant = "AIICO"
    broker = "ARK"

    def discover_premium_files(
        self, raw_dir: Path, year: int, quarter: int
    ) -> List[Tuple[int, Path]]:
        raw_dir = Path(raw_dir)
        months = QUARTER_MONTHS[quarter]
        found: List[Tuple[int, Path]] = []
        files = [
            p for p in raw_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".xlsx", ".xls"} and not p.name.startswith("~$")
        ]
        for month in months:
            match = self._match_premium(files, month, year)
            if match:
                found.append((month, match))
        return found

    def _match_premium(self, files: List[Path], month: int, year: int) -> Optional[Path]:
        name_token = MONTH_NAMES[month]
        short = {
            1: ["JAN", "JANUARY"],
            2: ["FEB", "FEBRUARY"],
            3: ["MAR", "MARCH"],
            4: ["APR", "APRIL"],
            5: ["MAY"],
            6: ["JUN", "JUNE"],
            7: ["JUL", "JULY"],
            8: ["AUG", "AUGUST"],
            9: ["SEP", "SEPT", "SEPTEMBER"],
            10: ["OCT", "OCTOBER"],
            11: ["NOV", "NOVEMBER"],
            12: ["DEC", "DECEMBER"],
        }[month]
        candidates = []
        for p in files:
            u = p.name.upper()
            # must look like premium, not claims
            if "CLAIM" in u:
                continue
            if "PREM" not in u and "PREMIUM" not in u:
                continue
            if not any(tok in u for tok in short):
                continue
            # year optional in filename (AUGUST PREMIUM LOCAL.xlsx)
            score = 10
            if str(year) in u:
                score += 5
            if name_token in u:
                score += 3
            candidates.append((score, p))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (-x[0], x[1].name))
        return candidates[0][1]

    def discover_claims_files(
        self, raw_dir: Path, year: int, quarter: int
    ) -> List[Path]:
        raw_dir = Path(raw_dir)
        q_patterns = {
            1: [r"1ST\s*QTR", r"Q1", r"FIRST\s*Q"],
            2: [r"2ND\s*QTR", r"Q2", r"SECOND\s*Q"],
            3: [r"3RD\s*QTR", r"Q3", r"THIRD\s*Q"],
            4: [r"4TH\s*QTR", r"Q4", r"FOURTH\s*Q"],
        }[quarter]
        out = []
        for p in sorted(raw_dir.iterdir()):
            if not p.is_file() or p.suffix.lower() not in {".xlsx", ".xls"}:
                continue
            if p.name.startswith("~$"):
                continue
            u = p.name.upper()
            if "CLAIM" not in u:
                continue
            if str(year) not in u and "CLAIM" in u:
                # still allow if quarter token matches strongly
                pass
            if any(re.search(pat, u) for pat in q_patterns):
                if str(year) in u or True:
                    # prefer year match
                    out.append(p)
        # Prefer files with year in name
        out_year = [p for p in out if str(year) in p.name]
        return out_year or out

    def month_label(self, month: int) -> str:
        return MONTH_NAMES.get(month, str(month))
