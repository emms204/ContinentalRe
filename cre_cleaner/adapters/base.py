"""Base adapter interface + discovery helpers shared by cedant adapters."""
from __future__ import annotations

import difflib
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from cre_cleaner.config import MONTH_ALIASES, MONTH_NAMES, QUARTER_MONTHS
from cre_cleaner.core.class_labels import ClassMap
from cre_cleaner.core.detect import GENERIC_SHEET_RULES, SheetTypeRules
from cre_cleaner.core.map_columns import (
    CLAIMS_ALIASES,
    GENERIC_PREMIUM_RULES,
    PREMIUM_ALIASES,
    ColumnMap,
    PremiumLayoutRules,
    detect_premium_allocation_blocks,
    map_simple_columns,
    merged_aliases,
)
from cre_cleaner.core.table_type import GENERIC_VOCAB, TableVocab

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


def claims_file_period_conflict(name: str, quarter: int) -> Optional[str]:
    """Why a claims/outstanding file name belongs to another quarter, else None.

    Run-time guard behind every adapter's claims discovery: a quarter must never
    load another quarter's claims file (e.g. '4TH Qtr 2021 Claims Bord.xls' in
    Q2). Only explicit whole-token quarters (quarters_in_text) or a single month
    name count; names without either are not judged here.
    """
    qs = quarters_in_text(name)
    if qs:
        return None if quarter in qs else f"file name says Q{'/Q'.join(map(str, sorted(qs)))}"
    months = {m for m in (token_month(t) for t in name_tokens(Path(name).stem)) if m}
    if len(months) == 1:
        m = next(iter(months))
        if m not in QUARTER_MONTHS[quarter]:
            return f"file name says {MONTH_NAMES.get(m, m)}"
    return None


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



def share_of_total(amount: Any, total: Any) -> Any:
    """amount / total as a fraction. Missing amount -> 0 when the total is
    usable; zero / missing / non-numeric total -> blank."""
    if total is None:
        return None
    try:
        denom = float(total)
    except (TypeError, ValueError):
        return None
    if denom == 0:
        return None
    if amount is None:
        return 0.0
    try:
        return float(amount) / denom
    except (TypeError, ValueError):
        return None

class UnsupportedCedantError(ValueError):
    """No adapter for this cedant/broker — never fall back to another cedant's rules."""


class BaseAdapter(ABC):
    cedant: str = ""
    broker: str = ""
    # False = first-pass adapter not yet checked against Bisola's cleaned output.
    verified: bool = True
    status_note: str = ""
    # --- cedant hooks (IMPL-20260929-04) ----------------------------------
    # Every cedant-specific name / rule is declared by the adapter through
    # these hooks; the shared core only knows generic, 100%-level names.
    premium_alias_extra: Dict[str, List[str]] = {}
    claims_alias_extra: Dict[str, List[str]] = {}
    # Premium RET / TREATY / FAC band vocabulary and layout rules.
    premium_layout_rules: PremiumLayoutRules = GENERIC_PREMIUM_RULES
    # Tab-name typing vocabulary (outstanding tokens, dump tabs, PAID tabs).
    sheet_type_rules: SheetTypeRules = GENERIC_SHEET_RULES
    # Header vocabulary used by content table typing (core.table_type).
    table_vocab: TableVocab = GENERIC_VOCAB
    # Extra source class → Bisola class keys (and short exact-only keys).
    class_label_extra: Dict[str, str] = {}
    class_label_exact_only: frozenset = frozenset()
    # RETENTION | TREATY band row + %/AMOUNT/SI sub-row header layout is
    # merged into one header when such a sub-row is present (generic, as
    # before IMPL-04; many cedants rely on it). False = adapter opts out.
    band_subrow_layout: bool = True
    # Explicit per-adapter settings (Bisola 2026-09-29 13:44 WAT: rules vary
    # by cedant, year and quarter). Resolution order, last wins:
    #   BASE_SETTINGS < settings < settings_by_period[(year, None)]
    #   < settings_by_period[(year, quarter)]
    # The values used are logged per sheet in the source-audit sidecar.
    #   uw_year_from_start_date: False = UW copied only from a source UW
    #     column; True = no UW column -> year of the policy start date
    #     (flagged uw_year_from_start_date). OFF by default everywhere.
    #   tsi_gp_basis: "100" (TSI / GP from the 100% columns) or "our_share"
    #     (from the cedant's our-share columns; honoured by adapters that
    #     have share columns).
    #   claims_ppn_calculated: False = claims PPN RET/TREATY/FAC copied from
    #     source PPN columns only (blank when none); True = when the tab has
    #     no PPN columns, PPN = band amount / total claims, every derived row
    #     flagged 'Calculated' in the source-audit notes.
    BASE_SETTINGS: Dict[str, Any] = {
        "uw_year_from_start_date": False, "tsi_gp_basis": "100", "claims_ppn_calculated": False,
    }
    SETTING_CHOICES: Dict[str, Tuple[Any, ...]] = {
        "uw_year_from_start_date": (False, True), "tsi_gp_basis": ("100", "our_share"),
        "claims_ppn_calculated": (False, True),
    }
    settings: Dict[str, Any] = {}
    settings_by_period: Dict[Tuple[int, Optional[int]], Dict[str, Any]] = {}
    # True = transaction type of every tab/table is decided from its content
    # (core.table_type) and in-period files outside the controlling set are
    # scanned for duplicates. False = name rules decide; content is advisory.
    content_sheet_typing: bool = False

    def __init__(self) -> None:
        # (severity, reason, filename, detail) raised during discovery
        self.discovery_notes: List[Tuple[str, str, str, str]] = []
        self.period: Tuple[Optional[int], Optional[int]] = (None, None)

    # --- settings -------------------------------------------------------
    def set_period(self, year: Optional[int], quarter: Optional[int]) -> None:
        """Run period used to resolve ``settings_by_period`` overrides."""
        self.period = (year, quarter)

    def setting_source(self, name: str) -> Tuple[Any, str]:
        """(value, where it came from) for one setting at the run period."""
        if name not in self.BASE_SETTINGS:
            raise KeyError(f"unknown adapter setting {name!r}")
        value, src = self.BASE_SETTINGS[name], "default"
        if name in self.settings:
            value, src = self.settings[name], "adapter"
        year, quarter = getattr(self, "period", (None, None))
        for key, label in (((year, None), f"{year}"), ((year, quarter), f"{year} Q{quarter}")):
            over = self.settings_by_period.get(key, {})
            if year is not None and name in over:
                value, src = over[name], f"override {label}"
        choices = self.SETTING_CHOICES.get(name)
        if choices is not None and value not in choices:
            raise ValueError(f"{type(self).__name__} setting {name}={value!r}; expected one of {choices}")
        return value, src

    def setting(self, name: str) -> Any:
        return self.setting_source(name)[0]

    def settings_note(self) -> str:
        """'settings: a=x (default), b=y (adapter)' for the audit sidecar."""
        parts = []
        for name in self.BASE_SETTINGS:
            value, src = self.setting_source(name)
            parts.append(f"{name}={value} ({src})")
        return "settings: " + ", ".join(parts)

    # --- column mapping -------------------------------------------------
    def premium_aliases(self) -> Dict[str, List[str]]:
        return merged_aliases(PREMIUM_ALIASES, self.premium_alias_extra)

    def claims_aliases(self) -> Dict[str, List[str]]:
        return merged_aliases(CLAIMS_ALIASES, self.claims_alias_extra)

    def premium_exclude(self, field: str, norm: str) -> bool:
        """Veto a column for a premium field (default: none)."""
        return False

    def claims_exclude(self, field: str, norm: str) -> bool:
        """Veto a column for a claims field (default: none)."""
        return False

    def detect_premium_allocation_blocks(
        self, header: Sequence[Any], group_row: Optional[Sequence[Any]],
    ) -> ColumnMap:
        """Premium columns + RET/TREATY/FAC bands with this adapter's rules."""
        return detect_premium_allocation_blocks(
            header, group_row, self.premium_aliases(), self.premium_layout_rules,
            exclude=self.premium_exclude,
        )

    def map_premium_columns(
        self,
        header: Sequence[Any],
        group_row: Optional[Sequence[Any]],
        *,
        path: Optional[Path] = None,
        sheet: str = "",
        exceptions: Optional[list] = None,
    ) -> ColumnMap:
        return self.detect_premium_allocation_blocks(header, group_row)

    def map_claims_columns(self, header: Sequence[Any], aliases: Dict[str, List[str]]) -> ColumnMap:
        return map_simple_columns(header, aliases, exclude=self.claims_exclude)

    def class_map(self) -> Optional[ClassMap]:
        """Generic class map + this adapter's extras (None = generic only)."""
        if not self.class_label_extra and not self.class_label_exact_only:
            return None
        cached = getattr(self, "_class_map_cache", None)
        if cached is None:
            cached = ClassMap(self.class_label_extra, self.class_label_exact_only)
            self._class_map_cache = cached
        return cached

    def derive_claims_ppn(self, amount: Any, total: Any) -> Any:
        """Claims PPN from amounts, used only when the setting
        ``claims_ppn_calculated`` is on (default: share of total claims)."""
        return share_of_total(amount, total)

    def check_premium_row(
        self, prow: Any, *, exceptions: list, path: Any, sheet: str, excel_row: int,
    ) -> None:
        """Adapter verification hook on a parsed premium row (flags only —
        must never change any value). Default: nothing."""
        return None

    def fix_claims_uw_details(self, uw: Any, details: Any) -> Optional[Tuple[Any, str]]:
        """Adapter repair of swapped UW YEAR / DETAILS cells (default none)."""
        return None

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

    def in_period_files(self, raw_dir: Path, year: int, quarter: int) -> List[Path]:
        """Excel files whose name places them in this quarter (a whole-token
        quarter, or exactly one month of the quarter) and not in another year.
        Used to find in-period files that discovery did not pick as controlling
        files (content scan + duplicate check)."""
        out = []
        for p in list_input_files(raw_dir, year):
            if p.suffix.lower() not in EXCEL_SUFFIXES or p.name.startswith("~$"):
                continue
            years = years_in_text(p.name)
            if years and year not in years:
                continue
            qs = quarters_in_text(p.name)
            months = {m for m in (token_month(t) for t in name_tokens(p.stem)) if m}
            if qs == {quarter} or (not qs and len(months) == 1
                                   and next(iter(months)) in QUARTER_MONTHS[quarter]):
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
