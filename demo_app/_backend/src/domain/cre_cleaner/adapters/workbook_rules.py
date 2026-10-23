"""Quarterly-workbook file selection and table re-typing shared by the EMPLE
and NSIA adapters. Cedant vocabulary stays in each adapter.

File selection follows the Cleaning Manual, Part Two step B: byte-identical
copies are read once, a named superseded copy is never read, and every
choice is written to the exceptions file. Quarters are read from file and
folder names including misspellings (``Secound Qtr``, ``QAURTER``) and
outstanding schedules named only by their as-at month or date.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from src.domain.cre_cleaner.adapters.base import (
    EXCEL_SUFFIXES,
    QuarterlyWorkbookAdapter,
    list_input_files,
    name_tokens,
    quarters_in_text,
    relative_label,
    token_month,
    years_in_text,
)
from src.domain.cre_cleaner.config import QUARTER_MONTHS
from src.domain.cre_cleaner.core.normalize import normalize_header

_ORDINALS = {
    "FIRST": 1, "1ST": 1, "SECOND": 2, "SECOUND": 2, "2ND": 2,
    "THIRD": 3, "3RD": 3, "FOURTH": 4, "4TH": 4,
}
_QUARTER_WORDS = {"QUARTER", "QAURTER", "QUATER", "QTR", "QRT", "QR"}
_DATE_IN_NAME = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)")


def _quarter_of_month(month: int) -> int:
    return next(q for q, months in QUARTER_MONTHS.items() if month in months)


def file_quarters(label: str) -> Set[int]:
    """Quarters a file or folder label names.

    An ordinal written before a quarter word controls (``4TH QUARTER 3 F`` is
    Q4, not Q3 and Q4). Otherwise the generic quarter patterns apply, and a
    label with no quarter at all is placed by its month names or its as-at
    date (``AS AT DEC 31 2020``, ``as at 31.12.2020``, ``JAN - MARCH``), but
    only when every month falls in one quarter.
    """
    toks = name_tokens(label)
    ordinal = {_ORDINALS[t] for i, t in enumerate(toks[:-1])
               if t in _ORDINALS and any(toks[i + 1].startswith(w) for w in _QUARTER_WORDS)}
    if ordinal:
        return ordinal
    found = quarters_in_text(label)
    if found:
        return found
    months = {m for m in (token_month(t) for t in toks) if m}
    months |= {int(m.group(2)) for m in _DATE_IN_NAME.finditer(label) if 1 <= int(m.group(2)) <= 12}
    quarters = {_quarter_of_month(m) for m in months}
    return quarters if len(quarters) == 1 else set()


def _same_name(a: str, b: str) -> bool:
    return " ".join(a.casefold().split()) == " ".join(b.casefold().split())


def _digest(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ControlledQuarterFiles(QuarterlyWorkbookAdapter):
    """Quarter discovery with documented controlling-file choices.

    ``superseded_files`` — {(year, quarter): {file name: reason}}: never read.
    A reason given as (severity, text) is logged at that severity (WARN when
    the copies disagree on values and the choice needs review).
    ``superseded_for_role`` — {(year, quarter, "premium"|"claims"): {name: reason}}:
    not read for that role only (e.g. a revised premium-only file controls
    premium while the combined original still supplies the claims).
    ``additive_quarters`` — {(year, quarter): reason}: several workbooks that
    are separate submissions (general and FAC-obligatory treaties); all are
    read instead of choosing one.

    ``premium_files_may_hold_claims`` — the cedant names combined workbooks
    'PREMIUM BORDEREAU'; with no combined workbook in the quarter, the chosen
    premium workbook is also read for its claims tabs (tabs are content-typed).
    """

    superseded_files: Dict[Tuple[int, int], Dict[str, str]] = {}
    premium_files_may_hold_claims: bool = False
    superseded_for_role: Dict[Tuple[int, int, str], Dict[str, str]] = {}
    additive_quarters: Dict[Tuple[int, int], str] = {}

    def _note(self, severity: str, reason: str, name: str, detail: str) -> None:
        note = (severity, reason, name, detail)
        if note not in self.discovery_notes:
            self.discovery_notes.append(note)

    def _quarter_files(self, raw_dir: Path, year: int, quarter: int) -> List[Path]:
        out: List[Path] = []
        for p in list_input_files(raw_dir, year):
            label = relative_label(raw_dir, p)
            if quarter not in file_quarters(label):
                continue
            years = years_in_text(p.name)
            if years and year not in years:
                if str(year) in Path(label).parts[:-1]:
                    self._note("INFO", "file_named_for_other_year", p.name,
                               f"{label}: file name says {sorted(years)}, not {year}; not read for {year}")
                continue
            if p.suffix.lower() not in EXCEL_SUFFIXES:
                self._note("INFO", "unsupported_file_type", p.name,
                           f"{label}: {p.suffix or 'no extension'} not read (Excel files only)")
                continue
            out.append(p)
        out = self._drop_identical(out)
        superseded = self.superseded_files.get((year, quarter), {})
        kept = []
        for p in out:
            reason = next((r for n, r in superseded.items() if _same_name(n, p.name)), None)
            if reason:
                severity, reason = reason if isinstance(reason, tuple) else ("INFO", reason)
                self._note(severity, "superseded_file_not_read", p.name, f"Q{quarter} {year}: {reason}")
            else:
                kept.append(p)
        return kept

    def _drop_identical(self, files: List[Path]) -> List[Path]:
        seen: Dict[str, Path] = {}
        kept = []
        for p in sorted(files, key=lambda x: (len(x.name), x.name)):
            d = _digest(p)
            if d in seen:
                self._note("INFO", "duplicate_file_not_read", p.name,
                           f"Byte-identical to {seen[d].name!r}; read once (Manual B.1)")
                continue
            seen[d] = p
            kept.append(p)
        return [p for p in files if p in kept]

    def _for_role(self, files: List[Path], role: str, year: int, quarter: int) -> List[Path]:
        drop = self.superseded_for_role.get((year, quarter, role), {})
        kept = []
        for p in files:
            reason = next((r for n, r in drop.items() if _same_name(n, p.name)), None)
            if reason:
                self._note("INFO", f"{role}_superseded_file_not_read", p.name, f"Q{quarter} {year}: {reason}")
            else:
                kept.append(p)
        return kept

    def _choose(self, files: List[Path], role: str, year: int, quarter: int) -> List[Path]:
        reason = self.additive_quarters.get((year, quarter))
        if reason and len(files) > 1:
            self._note("INFO", f"{role}_files_additive", files[0].name,
                       f"Q{quarter} {year}: {[p.name for p in files]} all read — {reason}")
            return files
        return self._pick(files, role, quarter)

    def discover_quarterly_premium_files(self, raw_dir: Path, year: int, quarter: int) -> List[Path]:
        self.discovery_notes = []
        files = self._for_role(
            [p for p in self._quarter_files(raw_dir, year, quarter) if "premium" in self._role(p)],
            "premium", year, quarter)
        combined = [p for p in files if "claims" in self._role(p)]
        premium_only = [p for p in files if "claims" not in self._role(p)]
        # As the base adapter: a combined workbook and a premium-only copy are
        # the same submission; prefer combined unless a role rule removed it.
        return self._choose(combined or premium_only, "premium", year, quarter)

    def discover_claims_files(self, raw_dir: Path, year: int, quarter: int) -> List[Path]:
        files = self._for_role(
            [p for p in self._quarter_files(raw_dir, year, quarter) if "claims" in self._role(p)],
            "claims", year, quarter)
        combined = [p for p in files if "premium" in self._role(p)]
        claims_only = [p for p in files if "premium" not in self._role(p)]
        picked = self._choose(combined, "claims", year, quarter) if combined else []
        if not picked and self.premium_files_may_hold_claims:
            saved = list(self.discovery_notes)
            picked = self.discover_quarterly_premium_files(raw_dir, year, quarter)
            self.discovery_notes = saved
            for p in picked:
                self._note("INFO", "premium_workbook_read_for_claims", p.name,
                           f"Q{quarter} {year}: no combined workbook; claims tabs of the premium workbook are read")
        return picked + claims_only

    def in_period_files(self, raw_dir: Path, year: int, quarter: int) -> List[Path]:
        out = []
        for p in list_input_files(raw_dir, year):
            if p.suffix.lower() not in EXCEL_SUFFIXES or p.name.startswith("~$"):
                continue
            years = years_in_text(p.name)
            if years and year not in years:
                continue
            if file_quarters(relative_label(raw_dir, p)) == {quarter}:
                out.append(p)
        return out


def terrorism_tab(sheet: str) -> bool:
    """Tab named for terrorism / political violence (alone or with FIRE)."""
    toks = set(name_tokens(sheet))
    return bool(toks & {"TERRORISM", "PVT"}) or "POLITICAL VIOLENCE" in " ".join(name_tokens(sheet))


TableRule = Callable[[Tuple[str, ...], Sequence[Sequence[object]], object], Optional[Tuple[str, str]]]


def retype_tables(file: str, sheet: str, rows: Sequence[Sequence[object]], vocab,
                  rule: Optional[TableRule] = None):
    """classify_tab plus three evidence rules for stacked class blocks:

    1. ``rule(header, rows, table)`` may type an UNKNOWN or mis-typed table
       from the cedant's own column vocabulary: returns (label, evidence).
    2. A block whose header row is identical to a block already typed in the
       same tab takes that type (a one-row block has too little evidence alone).
    3. A header with no data rows (OTHER) does not make the tab mixed.
    """
    from src.domain.cre_cleaner.core.table_type import OTHER, UNKNOWN, classify_tab

    typed = classify_tab(file, sheet, rows, vocab=vocab)

    def header_of(t):
        return tuple(normalize_header(v) for v in rows[t.header_row]) if t.header_row >= 0 else ()

    tables, changed = [], False
    for t in typed.tables:
        out = rule(header_of(t), rows, t) if rule and t.header_row >= 0 else None
        if out and out[0] != t.label:
            t = replace(t, label=out[0], confidence=1.0, conflicts=[], evidence=t.evidence + [out[1]])
            changed = True
        tables.append(t)
    known: Dict[Tuple[str, ...], Set[str]] = {}
    for t in tables:
        if t.header_row >= 0 and t.label not in (UNKNOWN, OTHER):
            known.setdefault(header_of(t), set()).add(t.label)
    for k, t in enumerate(tables):
        labels = known.get(header_of(t))
        if t.label == UNKNOWN and labels and len(labels) == 1:
            label = next(iter(labels))
            tables[k] = replace(t, label=label, confidence=1.0, conflicts=[],
                                evidence=t.evidence + [f"header identical to this tab's {label} block(s)"])
            changed = True
    counted = [t for t in tables if not (t.label == OTHER and t.header_row >= 0)]
    if not changed and len(counted) == len(tables):
        return typed
    labels = {t.label for t in counted} or {t.label for t in tables}
    return replace(typed, tables=tables,
                   label=next(iter(labels)) if len(labels) == 1 else UNKNOWN,
                   confidence=min(t.confidence for t in counted or tables),
                   conflicts=[c for t in counted for c in t.conflicts])
