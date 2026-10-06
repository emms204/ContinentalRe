"""Multi-file batches: one cleaning job per (cedant, broker, year, quarter).

IMPL-20260929-06. A multi-file upload is not one quarter: every file keeps its
own reporting period (same inference as a single-file run —
:func:`cre_cleaner.core.period_infer.infer_period`) and cedant, files are
grouped by (cedant, broker, year, quarter), and each group is cleaned as an
independent job. Premium, claims, outstanding and monthly files of one quarter
form one group. A failing group never stops or alters the others.

Files whose period cannot be settled (``period_ambiguous``), or whose cedant
awaits confirmation when none was selected (``cedant_unconfirmed`` /
``cedant_unknown``), are *pending*: listed for the user to set the year /
quarter / cedant (``overrides``) — never dropped. A selected cedant is
authoritative (IMPL-20260929-07): a file that seems to name another cedant is
cleaned under the selection with WARN ``cedant_mismatch_suspected``.

Nothing here is cedant-specific: cedant names come from the adapter registry.
"""
from __future__ import annotations

import re
import shutil
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.domain.cre_cleaner.core.period_infer import infer_period

EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}

# ERROR reasons that make a group's result unreliable (reported per group).
HARD_ERROR_REASONS = frozenset({
    "premium_file_unreadable", "claims_parse_failed", "no_premium_files",
    "no_claims_files", "period_unresolved", "period_ambiguous",
    "period_discovery_empty", "period_invalid", "bordereau_type_invalid",
    "claims_file_out_of_period", "batch_group_exception",
    "class_unresolved", "upload_structure_invalid",
})

STATUS_GROUPED = "grouped"
STATUS_PERIOD_AMBIGUOUS = "period_ambiguous"
STATUS_YEAR_MISMATCH = "year_mismatch"
STATUS_QUARTER_MISMATCH = "quarter_mismatch"
STATUS_PERIOD_UNINFERABLE = "period_uninferable"
STATUS_CEDANT_MISMATCH = "cedant_mismatch"   # legacy (pre IMPL-07); no longer set
STATUS_CEDANT_UNCONFIRMED = "cedant_unconfirmed"
STATUS_CEDANT_UNKNOWN = "cedant_unknown"
STATUS_NOT_EXCEL = "not_excel"


@dataclass
class FilePlan:
    path: Path
    cedant: str
    broker: str
    year: Optional[int] = None
    quarter: Optional[int] = None
    status: str = STATUS_GROUPED
    detail: str = ""
    period_source: str = "inferred"      # inferred | override
    cedant_source: str = "selected"      # selected | suggested | override
    cedant_warning: str = ""             # → WARN cedant_mismatch_suspected
    broker_warning: str = ""             # → WARN broker_mismatch_suspected
    period_warning: str = ""             # → WARN period_override_conflict
    confidence: str = ""
    evidence: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    detected_cedants: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name

    def as_dict(self) -> Dict[str, Any]:
        return {
            "file": self.name, "path": str(self.path), "cedant": self.cedant,
            "broker": self.broker, "year": self.year, "quarter": self.quarter,
            "status": self.status, "detail": self.detail,
            "period_source": self.period_source, "cedant_source": self.cedant_source,
            "confidence": self.confidence, "detected_cedants": list(self.detected_cedants),
            "cedant_warning": self.cedant_warning,
        }


@dataclass
class BatchGroup:
    cedant: str
    broker: str
    year: int
    quarter: int
    files: List[FilePlan] = field(default_factory=list)

    @property
    def key(self) -> Tuple[str, str, int, int]:
        return (self.cedant, self.broker, self.year, self.quarter)

    @property
    def label(self) -> str:
        return f"{self.cedant} / {self.broker} — {self.year} Q{self.quarter}"

    @property
    def slug(self) -> str:
        s = f"{self.cedant}_{self.broker}_{self.year}_Q{self.quarter}"
        return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


@dataclass
class BatchPlan:
    files: List[FilePlan] = field(default_factory=list)
    groups: List[BatchGroup] = field(default_factory=list)

    @property
    def pending(self) -> List[FilePlan]:
        return [f for f in self.files if f.status != STATUS_GROUPED]


@dataclass
class GroupResult:
    group: BatchGroup
    status: str = "ok"                    # ok | partial | failed
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    outputs: List[Dict[str, Any]] = field(default_factory=list)
    result: Any = None                    # PipelineResult (None when it raised)
    premium_rows: int = 0
    claims_rows: int = 0
    outstanding_rows: int = 0

    @property
    def ok(self) -> bool:
        return self.status != "failed"

    def as_dict(self) -> Dict[str, Any]:
        g = self.group
        return {
            "group": g.label, "cedant": g.cedant, "broker": g.broker,
            "year": g.year, "quarter": g.quarter,
            "files": [f.name for f in g.files], "status": self.status,
            "errors": list(self.errors), "warnings": list(self.warnings),
            "premium_rows": self.premium_rows, "claims_rows": self.claims_rows,
            "outstanding_rows": self.outstanding_rows,
            "outputs": [
                {k: v for k, v in o.items() if k != "summary"} for o in self.outputs
            ],
        }


@dataclass
class BatchResult:
    plan: BatchPlan
    groups: List[GroupResult] = field(default_factory=list)

    @property
    def pending(self) -> List[FilePlan]:
        return self.plan.pending

    def deliverable_outputs(self) -> List[Dict[str, Any]]:
        """Upload-ready workbook entries of every group that did not fail."""
        out = []
        for gr in self.groups:
            if gr.status == "failed":
                continue
            for o in gr.outputs:
                out.append(dict(o, group=gr.group.label, group_status=gr.status,
                                source_files=[f.name for f in gr.group.files],
                                year=gr.group.year, quarter=gr.group.quarter,
                                cedant=gr.group.cedant, broker=gr.group.broker))
        return out


# --- cedant detection (registry-driven, no names in this module) -----------
#
# IMPL-20260929-07. The user's cedant / broker choice is authoritative: a
# detected mismatch is only WARN cedant_mismatch_suspected. Evidence comes
# from the file name, the folder path and the title / banner rows ABOVE the
# table header — never data rows (insured names routinely contain words of
# other insurers' names). Only full registered names / codes match, as whole
# words; single-word aliases that are neither a registered name nor its
# spaced-out spelling are disabled for detection (they are ordinary words).

# Column-header tokens: a row that looks like a table header ends the banner
# area; everything below is data and never scanned.
_HEADER_HINTS = (
    "INSURED", "POLICY NO", "POLICY NUMBER", "DEBIT NOTE", "SUM INSURED",
    "GROSS PREMIUM", "DATE OF LOSS", "CLAIM NO", "PERIOD OF INSURANCE",
    "UW YEAR", "UNDERWRITING YEAR", "CLASS", "S/NO", "SNO",
)
# A row with this many filled cells is tabular (header or data), not a banner.
_TABULAR_MIN_CELLS = 4
_BANNER_SCAN_ROWS = 12
_FOLDER_PARTS = 4


def _registry() -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """(brokers by canonical cedant, alias cedant name → canonical)."""
    from src.domain.cre_cleaner.adapters import ADAPTERS, _ALIASES
    brokers: Dict[str, List[str]] = {}
    for c, b in ADAPTERS:
        brokers.setdefault(c, []).append(b)
    alias = {c: c for c in brokers}
    for (ac, _ab), (cc, _cb) in _ALIASES.items():
        alias.setdefault(ac, cc)
    return brokers, alias


def _tok(name: str) -> List[str]:
    return [t for t in re.split(r"[^A-Z0-9]+", str(name).upper()) if t]


def detection_names() -> Tuple[Dict[str, str], List[Tuple[str, str, str]]]:
    """(names used for detection → canonical cedant, disabled aliases).

    Kept: every registered cedant name / code; multi-word aliases; a
    single-word alias that is the registered name written without spaces.
    Disabled: any other single-word alias — it is a fragment of the name
    (an ordinary word that also occurs in other companies' names).
    Returns disabled entries as (alias, canonical, reason) for the audit.
    """
    brokers, alias = _registry()
    keep: Dict[str, str] = {}
    disabled: List[Tuple[str, str, str]] = []
    for name, canon in alias.items():
        toks, ctoks = _tok(name), _tok(canon)
        if name == canon or len(toks) >= 2 or "".join(toks) == "".join(ctoks):
            keep[name] = canon
        else:
            disabled.append((name, canon, "single-word fragment of the registered name"))
    return keep, sorted(disabled)


def _words(text: str) -> str:
    return " " + " ".join(_tok(text)) + " "


def _row_looks_like_header(cells: Sequence[str]) -> bool:
    joined = " ".join(cells).upper()
    hits = sum(1 for h in _HEADER_HINTS if h in joined)
    return hits >= 2 or (hits >= 1 and len(cells) >= 5)


def banner_rows(rows: Sequence[Sequence[Any]], max_rows: int = _BANNER_SCAN_ROWS) -> List[str]:
    """Text of the title / banner rows above the table header of one sheet.

    Stops at the first row that looks like a header or is tabular (at least
    ``_TABULAR_MIN_CELLS`` filled cells); nothing at or below it is returned.
    """
    out: List[str] = []
    for r in list(rows)[:max_rows]:
        cells = [str(c).strip() for c in r if c not in (None, "") and str(c).strip()]
        if not cells:
            continue
        if _row_looks_like_header(cells) or len(cells) >= _TABULAR_MIN_CELLS:
            break
        out.extend(cells)
    return out


def _banner_text(path: Path) -> str:
    try:
        from src.domain.cre_cleaner.io.excel import read_workbook_sheets
        sheets = read_workbook_sheets(path)
    except Exception:
        return ""
    parts: List[str] = []
    for rows in sheets.values():
        parts.extend(banner_rows(rows))
    return " ".join(parts)


def detect_cedants(
    path: Path,
    *,
    banner_text: Optional[str] = None,
    folder_parts: int = _FOLDER_PARTS,
) -> List[str]:
    """Registered cedants named (whole words) in the file name, the last
    ``folder_parts`` folder names, or the banner rows above the header.
    Longest names first so a multi-word name wins over its parts."""
    names, _disabled = detection_names()
    p = Path(path)
    sheet = banner_text if banner_text is not None else _banner_text(p)
    folders = " | ".join(p.parent.parts[-folder_parts:]) if folder_parts else ""
    hay = "  ".join(_words(t) for t in (p.stem, folders, sheet))
    found: List[str] = []
    for name in sorted(names, key=len, reverse=True):
        w = _words(name)
        if w.strip() and w in hay:
            canon = names[name]
            if canon not in found:
                found.append(canon)
            hay = hay.replace(w, " ")
    return found


# Broker names that are ordinary words are never evidence on their own.
_COMMON_WORD_BROKERS = frozenset({"DIRECT"})


def broker_detection_names(cedant: str) -> Dict[str, str]:
    """Names (canonical + alias) of the brokers registered for ``cedant`` →
    canonical broker. Single-word aliases that are only a fragment of the
    registered name, and brokers that are ordinary words, are left out."""
    from src.domain.cre_cleaner.adapters import ADAPTERS, _ALIASES
    c = str(cedant or "").strip().upper()
    names: Dict[str, str] = {}
    for (ac, ab) in ADAPTERS:
        if ac == c and ab not in _COMMON_WORD_BROKERS:
            names[ab] = ab
    for (ac, ab), (cc, cb) in _ALIASES.items():
        if cc != c or (cc, cb) not in ADAPTERS:
            continue
        toks, ctoks = _tok(ab), _tok(cb)
        if ab not in _COMMON_WORD_BROKERS and (len(toks) >= 2 or "".join(toks) == "".join(ctoks)):
            names.setdefault(ab, cb)
    return names


def detect_brokers(
    path: Path,
    cedant: str,
    *,
    banner_text: Optional[str] = None,
    folder_parts: int = _FOLDER_PARTS,
) -> List[str]:
    """Registered brokers of ``cedant`` named (whole words) in the file name,
    the last ``folder_parts`` folders or the banner rows above the header
    (IMPL-20261006-03 B19: the broker comes from evidence, not only from the
    partner folder). Longest names first."""
    names = broker_detection_names(cedant)
    if not names:
        return []
    p = Path(path)
    sheet = banner_text if banner_text is not None else _banner_text(p)
    folders = " | ".join(p.parent.parts[-folder_parts:]) if folder_parts else ""
    hay = "  ".join(_words(t) for t in (p.stem, folders, sheet))
    found: List[str] = []
    for name in sorted(names, key=len, reverse=True):
        w = _words(name)
        if w.strip() and w in hay:
            canon = names[name]
            if canon not in found:
                found.append(canon)
            hay = hay.replace(w, " ")
    return found


def broker_mismatch_warning(path: Path, cedant: str, broker: str,
                            *, banner_text: Optional[str] = None) -> str:
    """WARN text when the evidence names another registered broker of the
    selected cedant and not the selected broker; "" otherwise. The selection
    stays authoritative (same rule as cedants, IMPL-20260929-07)."""
    from src.domain.cre_cleaner.adapters import _ALIASES
    c = str(cedant or "").strip().upper()
    b = str(broker or "").strip().upper()
    b = _ALIASES.get((c, b), (c, b))[1]
    found = detect_brokers(path, c, banner_text=banner_text)
    others = [x for x in found if x != b]
    if not others or b in found:
        return ""
    return (
        f"{Path(path).name}: file name / folder / banner names broker "
        f"{', '.join(others)}, not the selected {c} / {b}; cleaned as {c} / {b} "
        "(selection is authoritative) — check the partner"
    )


def period_override_warning(name: str, inferred: Tuple[Optional[int], Optional[int]],
                            override: Tuple[int, int]) -> str:
    """WARN text when an explicit year/quarter differs from what the file
    itself says (IMPL-20261006-03 B7); "" when they agree or nothing was
    inferred."""
    iy, iq = inferred
    oy, oq = override
    if not iy or not iq or (int(iy), int(iq)) == (int(oy), int(oq)):
        return ""
    return (
        f"{name}: explicit period {oy} Q{oq} differs from the period the file "
        f"names / its dates give ({iy} Q{iq}); cleaned as {oy} Q{oq} as requested — "
        "check the year and quarter"
    )


# --- planning ---------------------------------------------------------------

def plan_batch(
    files: Iterable[Path],
    *,
    cedant: Optional[str],
    broker: Optional[str],
    overrides: Optional[Mapping[str, Mapping[str, Any]]] = None,
    detect_cedant: bool = True,
    selected_year: Optional[int] = None,
    selected_quarter: Optional[int] = None,
) -> BatchPlan:
    """Infer each file's period (and check its cedant) and group the files.

    * A selected ``cedant`` / ``broker`` is authoritative: a file whose name /
      folder / banner names a different registered cedant (and not the
      selected one) is still grouped under the selection, with WARN
      ``cedant_mismatch_suspected`` (``FilePlan.cedant_warning``).
    * No cedant selected (``None`` / ""): the same evidence may *suggest* one
      (status ``cedant_unconfirmed``) or none (``cedant_unknown``); either way
      the file waits until the user confirms a cedant / broker via
      ``overrides``.

    ``overrides`` maps a file name to any of ``year``, ``quarter``,
    ``cedant``, ``broker`` set by the user for that file.

    ``selected_year`` / ``selected_quarter`` (IMPL-20261006-06, corrected):
    the pick is the source of truth for filing/cleaning. Inference is
    evidence/audit only.

    * Strong year disagreement (inferred year set, confidence high/medium,
      year ≠ pick) → file ignored (``year_mismatch``).
    * Strong quarter disagreement in quarter mode → ``quarter_mismatch``.
    * Weak / empty / uninferable period → CLEAN under the selected year
      (and quarter when quarter mode); WARN via ``period_warning``.
    * Year mode without a quarter on an undated file still needs a quarter
      to place the workbook: leave ``period_ambiguous`` only when neither
      inference nor the pick supplies a quarter.
    """
    cedant = str(cedant or "").strip().upper()
    broker = str(broker or "").strip().upper()
    overrides = {str(k): dict(v) for k, v in (overrides or {}).items()}
    plan = BatchPlan()
    brokers, _alias = _registry()
    for p in sorted((Path(f) for f in files), key=lambda x: x.name.upper()):
        fp = FilePlan(path=p, cedant=cedant, broker=broker)
        ov = overrides.get(p.name, {})
        plan.files.append(fp)
        if p.suffix.lower() not in EXCEL_SUFFIXES:
            fp.status, fp.detail = STATUS_NOT_EXCEL, "not an Excel file (.xlsx/.xls/.xlsm)"
            continue

        found = detect_cedants(p) if detect_cedant else []
        fp.detected_cedants = found
        if ov.get("cedant"):
            fp.cedant = str(ov["cedant"]).strip().upper()
            fp.broker = str(ov.get("broker") or broker).strip().upper()
            fp.cedant_source = "override"
        elif cedant:
            pass  # the selection applies; checked against the evidence below
        else:
            sugg = [f"{c} / {b}" for c in found for b in brokers.get(c, [])]
            if len(found) == 1:
                fp.cedant = found[0]
                bl = brokers.get(found[0], [])
                fp.broker = bl[0] if len(bl) == 1 else ""
                fp.cedant_source = "suggested"
                fp.status = STATUS_CEDANT_UNCONFIRMED
                fp.detail = (
                    f"suggested {', '.join(sugg)} from the file name / folder / banner "
                    "— confirm the cedant / broker"
                )
            else:
                fp.status = STATUS_CEDANT_UNKNOWN
                fp.detail = (
                    ("several registered cedants named: " + ", ".join(sugg))
                    if found else "no registered cedant named in the file name / folder / banner"
                ) + " — choose the cedant / broker"

        # Selected / confirmed cedant is authoritative; contrary evidence → WARN.
        if fp.cedant and fp.status == STATUS_GROUPED:
            others = [c for c in found if c != fp.cedant]
            if others and fp.cedant not in found:
                fp.cedant_warning = (
                    f"{p.name}: file name / folder / banner names "
                    f"{', '.join(others)}, not the selected {fp.cedant} / {fp.broker}; "
                    f"cleaned as {fp.cedant} / {fp.broker} (selection is authoritative)"
                )

        if fp.cedant and fp.broker and fp.status == STATUS_GROUPED and detect_cedant:
            fp.broker_warning = broker_mismatch_warning(p, fp.cedant, fp.broker)

        # Period: the IMPL-05 single-file inference on this file alone.
        # A bare year folder anywhere in the path counts (IMPL-20261006-04):
        # infer_period -> filename_period reads the nearest one from ``p``.
        inf = infer_period(p)
        fp.confidence = inf.confidence
        fp.evidence = list(inf.evidence[:6])
        fp.warnings = list(inf.warnings) + ([inf.filename_override] if inf.filename_override else [])
        fp.warnings += [f"{reason}: {detail}" for sev, reason, detail in inf.notes if sev != "INFO"]
        fp.year, fp.quarter = inf.year, inf.quarter
        if ov.get("year") and ov.get("quarter"):
            fp.period_warning = period_override_warning(
                p.name, (inf.year, inf.quarter), (int(ov["year"]), int(ov["quarter"])),
            )
            fp.year, fp.quarter = int(ov["year"]), int(ov["quarter"])
            fp.period_source = "override"
        # IMPL-20261006-06 (corrected): selected year/quarter is authoritative.
        # Inference is evidence/audit. Strong disagreement → ignore file;
        # weak/empty → clean under the pick and WARN.
        strong_conf = (fp.confidence or "").lower() in ("high", "medium")
        if fp.status == STATUS_GROUPED and selected_year is not None:
            inferred_y, inferred_q = fp.year, fp.quarter
            # Strong year mismatch: clear inferred year disagrees with pick.
            if (
                inferred_y is not None
                and int(inferred_y) != int(selected_year)
                and strong_conf
                and not (ov.get("year") and ov.get("quarter"))
            ):
                fp.status = STATUS_YEAR_MISMATCH
                fp.detail = (
                    f"inferred year {inferred_y} (confidence={fp.confidence}) ≠ "
                    f"selected year {selected_year} — file ignored, not cleaned"
                )
            elif (
                selected_quarter is not None
                and inferred_y is not None
                and int(inferred_y) == int(selected_year)
                and inferred_q in (1, 2, 3, 4)
                and int(inferred_q) != int(selected_quarter)
                and strong_conf
                and not (ov.get("year") and ov.get("quarter"))
            ):
                fp.status = STATUS_QUARTER_MISMATCH
                fp.detail = (
                    f"inferred Q{inferred_q} (confidence={fp.confidence}) ≠ "
                    f"selected Q{selected_quarter} for {selected_year} — "
                    "file ignored in quarter mode"
                )
            else:
                # Apply the pick when inference is weak/empty or agrees.
                use_y = int(selected_year)
                use_q = (
                    int(selected_quarter)
                    if selected_quarter is not None
                    else (int(inferred_q) if inferred_q in (1, 2, 3, 4) else None)
                )
                if use_q in (1, 2, 3, 4):
                    if (inferred_y, inferred_q) != (use_y, use_q):
                        fp.period_warning = (
                            f"{p.name}: inference year={inferred_y} Q{inferred_q} "
                            f"(confidence={fp.confidence or 'none'}); cleaned as "
                            f"{use_y} Q{use_q} (selection is authoritative)"
                        )
                        fp.period_source = fp.period_source or "selection"
                    fp.year, fp.quarter = use_y, use_q
                else:
                    # Year mode, no quarter from pick or inference → still ambiguous.
                    fp.status = STATUS_PERIOD_AMBIGUOUS
                    fp.detail = (
                        f"reporting period not settled (year={inferred_y}, quarter={inferred_q}); "
                        f"selected year {selected_year} but no quarter to place the workbook — "
                        "set quarter (picker or per-file override) or fix source dates"
                    )
        elif fp.status == STATUS_GROUPED and not (fp.year and fp.quarter in (1, 2, 3, 4)):
            fp.status = STATUS_PERIOD_AMBIGUOUS
            fp.detail = (
                f"reporting period not settled (year={fp.year}, quarter={fp.quarter}) — "
                "set the year and quarter for this file"
            )

    groups: Dict[Tuple[str, str, int, int], BatchGroup] = {}
    for fp in plan.files:
        if fp.status != STATUS_GROUPED:
            continue
        key = (fp.cedant, fp.broker, int(fp.year), int(fp.quarter))
        if key not in groups:
            groups[key] = BatchGroup(*key)
        groups[key].files.append(fp)
    plan.groups = sorted(groups.values(), key=lambda g: (g.cedant, g.broker, g.year, g.quarter))
    return plan


# --- running ----------------------------------------------------------------

def _count(o: Dict[str, Any], k: str) -> int:
    try:
        return int(o.get(k) or 0)
    except (TypeError, ValueError):
        return 0


def run_group(
    group: BatchGroup,
    *,
    template: Path,
    out_dir: Path,
    work_dir: Path,
    base_dir: Optional[Path] = None,
    runner: Optional[Callable[..., Any]] = None,
    **pipeline_kwargs: Any,
) -> GroupResult:
    """Clean one group in its own raw folder; never raises."""
    if runner is None:
        from src.domain.cre_cleaner.pipeline import run_pipeline as runner
    gr = GroupResult(group=group)
    try:
        raw = Path(work_dir) / group.slug / "raw"
        if raw.exists():
            shutil.rmtree(raw)
        raw.mkdir(parents=True)
        used_names: set = set()
        for n, fp in enumerate(group.files, 1):
            # Two group files with one name (different upload folders) must
            # not overwrite each other (IMPL-20261006-03 B2): the second goes
            # to its own sub-folder and keeps its name for period inference.
            dest = raw / fp.name
            if fp.name.upper() in used_names:
                dest = raw / f"_src{n}" / fp.name
                dest.parent.mkdir(parents=True, exist_ok=True)
            used_names.add(fp.name.upper())
            shutil.copy2(fp.path, dest)
        from src.domain.cre_cleaner.models import ExceptionRecord
        extra = []
        for fp in group.files:
            for reason, text in (
                ("cedant_mismatch_suspected", fp.cedant_warning),
                ("broker_mismatch_suspected", getattr(fp, "broker_warning", "")),
                ("period_override_conflict", getattr(fp, "period_warning", "")),
            ):
                if text:
                    extra.append(ExceptionRecord("WARN", reason, fp.name, detail=text))
                    gr.warnings.append(f"{reason}: {text}")
        if extra:
            pipeline_kwargs = dict(pipeline_kwargs, extra_exceptions=extra)
        res = runner(
            cedant=group.cedant, broker=group.broker,
            year=group.year, quarter=group.quarter,
            raw_dir=raw, template=template, out_dir=out_dir, base_dir=base_dir,
            # The group's files ARE the quarter: no filename quarter gate.
            single_file=True,
            **pipeline_kwargs,
        )
        gr.result = res
        gr.outputs = [dict(o) for o in (getattr(res, "outputs", None) or [])]
        for e in getattr(res, "exceptions", []) or []:
            if getattr(e, "severity", "") == "ERROR" and getattr(e, "reason", "") in HARD_ERROR_REASONS:
                src = getattr(e, "source_filename", "") or ""
                gr.errors.append(f"{e.reason}: {src + ': ' if src else ''}{e.detail}")
    except Exception as exc:  # isolation: report, never propagate
        gr.errors.append(f"batch_group_exception: {exc!r}")
        gr.errors.append(traceback.format_exc(limit=3).strip().splitlines()[-1])
        gr.outputs = []
    gr.premium_rows = sum(_count(o, "premium_rows") for o in gr.outputs)
    gr.claims_rows = sum(_count(o, "claims_rows") for o in gr.outputs)
    gr.outstanding_rows = sum(_count(o, "outstanding_rows") for o in gr.outputs)
    total = gr.premium_rows + gr.claims_rows + gr.outstanding_rows
    if not gr.outputs or (total == 0 and gr.errors):
        gr.status = "failed"
    elif gr.errors:
        gr.status = "partial"
    else:
        gr.status = "ok"
    return gr


def run_batch(
    plan: BatchPlan,
    *,
    template: Path,
    out_dir: Path,
    work_dir: Path,
    base_dir: Optional[Path] = None,
    runner: Optional[Callable[..., Any]] = None,
    on_group: Optional[Callable[[BatchGroup, int, int], None]] = None,
    **pipeline_kwargs: Any,
) -> BatchResult:
    """Run every group independently (pending files are not run)."""
    out = BatchResult(plan=plan)
    n = len(plan.groups)
    for i, g in enumerate(plan.groups, 1):
        if on_group:
            try:
                on_group(g, i, n)
            except Exception:
                pass
        out.groups.append(run_group(
            g, template=template, out_dir=out_dir, work_dir=work_dir,
            base_dir=base_dir, runner=runner, **pipeline_kwargs,
        ))
    return out
