"""Base adapter interface + discovery helpers shared by cedant adapters."""
from __future__ import annotations

import difflib
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from cre_cleaner.config import MONTH_ALIASES, MONTH_NAMES
from cre_cleaner.core.map_columns import (
    CLAIMS_ALIASES,
    PREMIUM_ALIASES,
    ColumnMap,
    detect_premium_allocation_blocks,
    merged_aliases,
)

EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}

_MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}
_QUARTER_WORDS = {"FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4,
                  "1ST": 1, "2ND": 2, "3RD": 3, "4TH": 4}
_QUARTER_TOKENS = {"QTR", "QUARTER", "QUATER", "QTR.", "QR"}


def name_tokens(text: str) -> List[str]:
    return [t for t in re.split(r"[^A-Z0-9]+", str(text).upper()) if t]


def token_month(tok: str) -> Optional[int]:
    """Month for a filename token: alias (JAN, SEPT, OCTOBER) or an obvious
    misspelling of the full name (FEBURARY, SEPTERMBER)."""
    if tok in MONTH_ALIASES:
        return MONTH_ALIASES[tok]
    if len(tok) >= 5 and tok.isalpha():
        for m, abbr in _MONTH_ABBR.items():
            if tok.startswith(abbr):
                if difflib.SequenceMatcher(None, tok, MONTH_NAMES[m]).ratio() >= 0.8:
                    return m
    return None


def quarters_in_text(text: str) -> Set[int]:
    """Quarters named in a file/folder name: Q1, 'Q 1', 'BordreauxQ4', '1ST QTR',
    'FIRST QUARTER', 'QR 1', '2ND QUATER', 'QTR1', '1QTR'.

    Must not treat the leading digit of a year as a quarter: ``Qtr 2021`` is not Q2.
    """
    u = str(text).upper()
    # (?!\d) blocks "Qtr 2021" → false Q2 (the "2" of 2021).
    found = {int(m.group(1)) for m in re.finditer(r"Q\s*([1-4])(?!\d)", u)}
    found |= {int(m.group(1)) for m in re.finditer(r"QTR\s*([1-4])(?!\d)", u)}
    found |= {int(m.group(1)) for m in re.finditer(r"(?<!\d)([1-4])\s*QTR\b", u)}
    toks = name_tokens(u)
    for i, t in enumerate(toks):
        if t in _QUARTER_TOKENS:
            if i > 0 and toks[i - 1] in _QUARTER_WORDS:
                found.add(_QUARTER_WORDS[toks[i - 1]])
            if i + 1 < len(toks) and toks[i + 1] in {"1", "2", "3", "4"}:
                found.add(int(toks[i + 1]))
        m = re.fullmatch(r"QTR([1-4])", t) or re.fullmatch(r"([1-4])QTR", t)
        if m:
            found.add(int(m.group(1)))
    return found


def years_in_text(text: str) -> Set[int]:
    return {int(t) for t in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", str(text))}


def list_input_files(raw_dir: Path, year: Optional[int] = None) -> List[Path]:
    """Every file under raw_dir (or raw_dir itself when it is one file).
    Sub-folders named for a different year are not entered; lock/hidden files
    are skipped."""
    raw_dir = Path(raw_dir)
    if raw_dir.is_file():
        return [raw_dir]
    out: List[Path] = []
    for p in sorted(raw_dir.rglob("*")):
        rel_parts = p.relative_to(raw_dir).parts
        if any(part.startswith(".") or part.startswith("__MACOSX") for part in rel_parts):
            continue
        if year is not None and any(
            re.fullmatch(r"(19|20)\d{2}", part) and int(part) != year for part in rel_parts[:-1]
        ):
            continue
        if p.is_file() and not p.name.startswith("~$"):
            out.append(p)
    return out


def relative_label(raw_dir: Path, p: Path) -> str:
    raw_dir = Path(raw_dir)
    if raw_dir.is_file():
        return p.name
    try:
        return str(p.relative_to(raw_dir))
    except ValueError:
        return p.name


def pick_controlling_file(cands: Sequence[Path]) -> Tuple[Path, List[Path], bool]:
    """(chosen, others, tie). Revised/updated/amended copies win, then
    non-'copy' files, then the most recently modified."""
    def score(p: Path) -> tuple:
        toks = name_tokens(p.stem)
        revised = any(t in {"REVISED", "UPDATED", "AMENDED", "REVIEWED"} for t in toks)
        copy = "COPY" in toks or bool(re.search(r"\(\d+\)", p.stem))
        return (revised, not copy)

    ranked = sorted(cands, key=lambda p: (tuple(not x for x in score(p)), -p.stat().st_mtime, p.name))
    tie = len(ranked) > 1 and score(ranked[0]) == score(ranked[1])
    return ranked[0], ranked[1:], tie


class UnsupportedCedantError(ValueError):
    """No adapter for this cedant/broker — never fall back to another cedant's rules."""


class BaseAdapter(ABC):
    cedant: str = ""
    broker: str = ""
    # False = first-pass adapter not yet checked against Bisola's cleaned output.
    verified: bool = True
    status_note: str = ""
    premium_alias_extra: Dict[str, List[str]] = {}
    claims_alias_extra: Dict[str, List[str]] = {}

    def __init__(self) -> None:
        # (severity, reason, filename, detail) raised during discovery
        self.discovery_notes: List[Tuple[str, str, str, str]] = []

    # --- column mapping -------------------------------------------------
    def premium_aliases(self) -> Dict[str, List[str]]:
        return merged_aliases(PREMIUM_ALIASES, self.premium_alias_extra)

    def claims_aliases(self) -> Dict[str, List[str]]:
        return merged_aliases(CLAIMS_ALIASES, self.claims_alias_extra)

    def map_premium_columns(
        self,
        header: Sequence[Any],
        group_row: Optional[Sequence[Any]],
        *,
        path: Optional[Path] = None,
        sheet: str = "",
        exceptions: Optional[list] = None,
    ) -> ColumnMap:
        return detect_premium_allocation_blocks(header, group_row, self.premium_aliases())

    # --- discovery ------------------------------------------------------
    @abstractmethod
    def discover_premium_files(self, raw_dir: Path, year: int, quarter: int) -> List[Tuple[int, Path]]:
        """Monthly premium files: list of (month_number, path) in calendar order."""

    @abstractmethod
    def discover_claims_files(self, raw_dir: Path, year: int, quarter: int) -> List[Path]:
        """Quarterly claims bordereau paths."""

    def discover_quarterly_premium_files(self, raw_dir: Path, year: int, quarter: int) -> List[Path]:
        """Premium files covering the whole quarter (e.g. '4th Qtr 2021 Premium
        ceded.xls', 'Premium n Loss Bordereaux Q1.xlsx', 'QTR1 BORD.xlsx'). The
        pipeline reads these only when no monthly premium file exists — never both."""
        out = []
        for p in list_input_files(raw_dir, year):
            if p.suffix.lower() not in EXCEL_SUFFIXES:
                continue
            label = relative_label(raw_dir, p)
            toks = name_tokens(p.stem)
            has_prem = any(t.startswith("PREM") or t.startswith("BORD") for t in toks)
            # "QTR1.xlsx" / "QTR1 RETURNS.xlsx" with no CLAIM token still counts
            has_q_only = bool(quarters_in_text(label)) and not any(
                t in {"CLAIM", "CLAIMS", "LOSS", "LOSSES", "PAID", "OUTSTANDING"} for t in toks
            )
            if not has_prem and not has_q_only:
                continue
            if any(t in {"CLAIM", "CLAIMS", "PAID"} for t in toks) and not has_prem:
                continue
            if quarter not in quarters_in_text(label):
                continue
            years = years_in_text(p.name)
            if years and year not in years:
                continue
            # Skip files that are clearly monthly (have a month and no quarter)
            months = {m for m in (token_month(t) for t in toks) if m}
            if months and not quarters_in_text(label):
                continue
            out.append(p)
        return out

    def month_label(self, month: int) -> str:
        return MONTH_NAMES.get(month, str(month))

    def status_text(self) -> str:
        if self.verified:
            return "verified against Bisola's cleaned workbooks"
        return "UNVERIFIED first-pass adapter — not yet checked against Bisola's output" + (
            f" ({self.status_note})" if self.status_note else ""
        )


class QuarterlyWorkbookAdapter(BaseAdapter):
    """Cedants that send one or a few workbooks per quarter (premium and claims
    often in the same file). Files are matched by quarter in the file or
    folder name; each workbook's sheets are typed by the parser, so a combined
    workbook feeds both the premium and the claims side."""

    verified = False
    _CLAIM_TOKENS = {"CLAIM", "CLAIMS", "CLAIMD", "LOSS", "LOSSES", "OUTSTANDING", "OUST", "OUTS",
                     "OST", "OS", "PAID", "RECOVERY"}
    _PREMIUM_TOKENS_PREFIX = "PREM"

    def discover_premium_files(self, raw_dir: Path, year: int, quarter: int) -> List[Tuple[int, Path]]:
        return []

    def _quarter_files(self, raw_dir: Path, year: int, quarter: int) -> List[Path]:
        out = []
        for p in list_input_files(raw_dir, year):
            label = relative_label(raw_dir, p)
            if quarter not in quarters_in_text(label):
                continue
            years = years_in_text(p.name)
            if years and year not in years:
                continue
            if p.suffix.lower() not in EXCEL_SUFFIXES:
                note = (
                    "INFO", "unsupported_file_type", p.name,
                    f"{label}: {p.suffix or 'no extension'} not read (Excel .xlsx/.xls only"
                    + ("; unzip it first" if p.suffix.lower() == ".zip" else "") + ")",
                )
                if note not in self.discovery_notes:
                    self.discovery_notes.append(note)
                continue
            out.append(p)
        return out

    def _role(self, p: Path) -> Set[str]:
        toks = set(name_tokens(p.stem))
        has_prem = any(t.startswith(self._PREMIUM_TOKENS_PREFIX) for t in toks)
        has_claim = bool(toks & self._CLAIM_TOKENS)
        if has_prem and not has_claim:
            return {"premium"}
        if has_claim and not has_prem:
            return {"claims"}
        return {"premium", "claims"}  # combined or unnamed returns: sheets decide

    def _pick(self, files: List[Path], role: str, quarter: int) -> List[Path]:
        """One controlling file per role; alternatives (e.g. a revised copy) are
        logged, never read twice."""
        if len(files) <= 1:
            return files
        chosen, others, tie = pick_controlling_file(files)
        self.discovery_notes.append((
            "WARN" if tie else "INFO",
            f"{role}_quarter_ambiguous" if tie else f"{role}_quarter_multiple_files",
            chosen.name,
            f"Q{quarter}: {len(files)} candidate {role} files; using {chosen.name!r}, not reading "
            f"{[p.name for p in others]}" + (" (tie — please confirm controlling file)" if tie
                                            else " (revised / non-copy preferred)"),
        ))
        return [chosen]

    def discover_quarterly_premium_files(self, raw_dir: Path, year: int, quarter: int) -> List[Path]:
        self.discovery_notes = []
        files = [p for p in self._quarter_files(raw_dir, year, quarter) if "premium" in self._role(p)]
        by_kind: Dict[bool, List[Path]] = {True: [], False: []}
        for p in files:
            by_kind["claims" in self._role(p)].append(p)
        # Premium-only files and combined files are separate submissions (a
        # combined 'Premium n Loss' workbook plus a standalone premium file
        # would double count): prefer combined, else premium-only.
        return self._pick(by_kind[True] or by_kind[False], "premium", quarter)

    def discover_claims_files(self, raw_dir: Path, year: int, quarter: int) -> List[Path]:
        files = [p for p in self._quarter_files(raw_dir, year, quarter) if "claims" in self._role(p)]
        combined = [p for p in files if "premium" in self._role(p)]
        claims_only = [p for p in files if "premium" not in self._role(p)]
        picked = self._pick(combined, "claims", quarter) if combined else []
        # Separate paid / outstanding files are distinct submissions, not copies.
        return picked + claims_only
