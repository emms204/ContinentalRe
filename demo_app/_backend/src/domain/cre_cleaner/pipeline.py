"""Orchestrate one cre_cleaner run."""
from __future__ import annotations

import functools
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from src.domain.cre_cleaner.adapters import get_adapter
from src.domain.cre_cleaner.core.class_labels import (
    claims_class_hint,
    format_unapproved_classes,
    group_rows_by_class,
    is_fac_class,
    is_unresolved_class,
    ordered_class_labels,
    premium_class_hint,
    unapproved_class_rows,
)
from src.domain.cre_cleaner.core.class_suggest import unresolved_class_entries
from src.domain.cre_cleaner.core.class_aliases import (
    ALIAS_STORE_UNAVAILABLE,
    CLASS_ALIAS_APPLIED,
    CLASS_ALIASES_LOADED,
    ConfirmedAliases,
    alias_applications,
    confirmed_label,
    with_confirmed_aliases,
)
from src.domain.cre_cleaner.config import QUARTER_MONTHS
from src.domain.cre_cleaner.io.excel import workbook_read_cache, write_output_workbook
from src.domain.cre_cleaner.models import PipelineResult, ExceptionRecord
from src.domain.cre_cleaner.timing_log import log_timing
from src.domain.cre_cleaner.io.pdf import convert_pdfs_in_dir, list_pdfs
from src.domain.cre_cleaner.core.period_infer import folder_year, infer_period, _iter_excel_paths
from src.domain.cre_cleaner.adapters.base import claims_file_period_conflict
from src.domain.cre_cleaner.core.quarterly import merge_monthly_premiums, parse_claims_file, parse_premium_file
from src.domain.cre_cleaner.core.reconcile import (
    build_summary,
    build_source_reconciliation,
    printed_total_variances,
    check_row_dates,
    check_row_splits,
    flag_duplicate_claims,
    flag_duplicate_premium,
    overlap_counts,
    transaction_key,
)


def _by_class_counts(premium_rows, claims_rows, outstanding_rows, class_map=None) -> dict:
    prem_by = group_rows_by_class(premium_rows, premium_class_hint, class_map)
    paid_by = group_rows_by_class(claims_rows, claims_class_hint, class_map)
    ost_by = group_rows_by_class(outstanding_rows, claims_class_hint, class_map)
    labels = ordered_class_labels(set(prem_by) | set(paid_by) | set(ost_by))
    out = {}
    for lab in labels:
        out[lab] = {
            "premium": len(prem_by.get(lab) or []),
            "claims": len(paid_by.get(lab) or []),
            "outstanding": len(ost_by.get(lab) or []),
        }
    return out


def _drop_fac_rows(premium_rows, claims_rows, outstanding_rows, class_map=None):
    """Exclude Facultative-class rows from upload output (Continental guidance)."""
    prem = [r for r in premium_rows if not is_fac_class(premium_class_hint(r), class_map)]
    paid = [r for r in claims_rows if not is_fac_class(claims_class_hint(r), class_map)]
    ost = [r for r in outstanding_rows if not is_fac_class(claims_class_hint(r), class_map)]
    return prem, paid, ost



def _label_is_ignored(label: str, file: str, sheet: str, ignored) -> bool:
    """True when ``ignored`` (list of {label, file?, sheet?}) covers this row."""
    if not ignored:
        return False
    from src.domain.cre_cleaner.core.class_suggest import normalize_for_suggestion
    raw = (label or "").strip()
    raw_key = normalize_for_suggestion(raw) or raw.upper()
    for item in ignored:
        if isinstance(item, dict):
            want = str(item.get("label") or "").strip()
            f, sh = item.get("file"), item.get("sheet")
        else:
            want = str(getattr(item, "label", "") or "").strip()
            f = getattr(item, "file", None) or None
            sh = getattr(item, "sheet", None) or None
        if not want:
            continue
        want_key = normalize_for_suggestion(want) or want.upper()
        if want_key != raw_key and want.upper() != raw.upper():
            continue
        if f and str(f) != str(file or ""):
            continue
        if sh and str(sh) != str(sheet or ""):
            continue
        return True
    return False


def _partition_ignored(blocked, ignored):
    """Split blocked ``(label, file, sheet, count)`` into still-blocked vs ignored."""
    still, dropped = [], []
    for item in blocked or []:
        label, fname, sheet, count = item[0], item[1], item[2], item[3]
        if _label_is_ignored(label, fname, sheet, ignored):
            dropped.append({"label": label, "file": fname or "", "sheet": sheet or "",
                            "records": int(count), "reason": "user_ignored"})
        else:
            still.append(item)
    return still, dropped


def _drop_ignored_rows(rows, class_getter, ignored):
    """Drop rows whose class label the caller chose to ignore."""
    if not ignored:
        return list(rows or []), 0
    kept, n = [], 0
    for r in rows or []:
        hint = (class_getter(r) or "").strip() or "(blank)"
        a = getattr(r, "audit", None)
        fname = getattr(a, "source_filename", "") if a else ""
        sheet = getattr(a, "source_sheet", "") if a else ""
        if _label_is_ignored(hint if hint != "(blank)" else "", fname, sheet, ignored) or (
            hint == "(blank)" and _label_is_ignored("(blank)", fname, sheet, ignored)
        ):
            n += 1
            continue
        # also match on raw hint without blank sentinel
        if hint != "(blank)" and _label_is_ignored(hint, fname, sheet, ignored):
            n += 1
            continue
        kept.append(r)
    return kept, n


def _divert_unresolved_class(rows, class_getter, bordereau: str, blocked: Optional[list] = None,
                             class_map=None):
    """Rows with no class, or a class too broad to place (bare MARINE), are
    NOT written to an 'Other' sheet; they go to the exceptions sidecar.

    IMPL-20261006-03 (B10): they also block the clean like any other
    unresolved class — each label (``(blank)`` when there is none) is added
    to ``blocked`` as ``(label, file, sheet, count)``, so single-file and
    multi-file cleans both answer 422 with the label in ``unresolved[]``
    (bare MARINE: ``marine_hull_vs_cargo_ambiguous``). Rows are never
    silently dropped from a 200.

    IMPL-20261006-05: a label the partner's class store places (an alias a
    person confirmed for this partner, e.g. MARINE -> Marine Hull) is kept."""
    kept, excs = [], []
    buckets: dict = {}
    for r in rows:
        hint = (class_getter(r) or "").strip()
        if hint and (not is_unresolved_class(hint) or confirmed_label(hint, class_map)):
            kept.append(r)
            continue
        a = getattr(r, "audit", None)
        bkey = (
            hint or "(blank)",
            getattr(a, "source_filename", "") if a else "",
            getattr(a, "source_sheet", "") if a else "",
        )
        buckets[bkey] = buckets.get(bkey, 0) + 1
        why = (
            f"bare class {hint!r} is too broad to place"
            if hint
            else "no class from tab/row/section banner"
        )
        if bordereau == "PREMIUM":
            detail = (
                f"{bordereau}: {why} — kept out of upload; "
                f"policy={getattr(r, 'policy_no', '')!r} "
                f"insured={getattr(r, 'name_of_insured', '')!r} "
                f"gross={getattr(r, 'gross_premium', None)}"
            )
        else:
            detail = (
                f"{bordereau}: {why} — kept out of upload; "
                f"claim={getattr(r, 'claim_no', '')!r} policy={getattr(r, 'policy_no', '')!r} "
                f"insured={getattr(r, 'insured', '')!r} total={getattr(r, 'total_claims', None)}"
            )
        excs.append(ExceptionRecord(
            "WARN", "class_unresolved",
            getattr(a, "source_filename", "") if a else "",
            getattr(a, "source_sheet", "") if a else "",
            getattr(a, "source_row", 0) if a else 0,
            detail,
        ))
    if blocked is not None:
        blocked.extend((label, fname, sheet, n) for (label, fname, sheet), n in buckets.items())
    return kept, excs


def _row_currency(row) -> str:
    return getattr(getattr(row, "audit", None), "currency", None) or "NGN"


def _split_by_currency(
    premium_rows, claims_rows, outstanding_rows,
) -> List[Tuple[str, list, list, list]]:
    """One group per currency. NGN first, then others alphabetically. Never
    sum across currencies."""
    currencies = sorted(
        {_row_currency(r) for r in list(premium_rows) + list(claims_rows) + list(outstanding_rows)},
        key=lambda c: (c != "NGN", c),
    )
    if not currencies:
        return [("NGN", [], [], [])]
    out = []
    for ccy in currencies:
        out.append((
            ccy,
            [r for r in premium_rows if _row_currency(r) == ccy],
            [r for r in claims_rows if _row_currency(r) == ccy],
            [r for r in outstanding_rows if _row_currency(r) == ccy],
        ))
    return out


def _filter_exceptions_for_currency(exceptions: Sequence[ExceptionRecord], currency: str,
                                    source_audit) -> List[ExceptionRecord]:
    """Keep workbook-level exceptions plus those whose source sheet matches
    this currency (or MIXED / unknown)."""
    sheet_ccy = {(a.source_filename, a.source_sheet): a.currency for a in source_audit}
    kept = []
    for e in exceptions:
        key = (e.source_filename, e.source_sheet)
        sc = sheet_ccy.get(key)
        if sc is None or sc in (currency, "MIXED", ""):
            kept.append(e)
    return kept


def _filter_audit_for_currency(source_audit, currency: str):
    return [
        a for a in source_audit
        if a.currency in (currency, "MIXED", "")
        or (a.parsed_rows and currency in a.parsed_rows)
    ]


def _discover_premium_inputs(adapter, raw_dir: Path, year: int, quarter: int):
    """Monthly premium files when present; otherwise quarterly premium files
    (never both). Returns (month_files, mode) where mode is 'monthly' |
    'quarterly' | 'none'."""
    prem_discovered = adapter.discover_premium_files(raw_dir, year, quarter)
    if prem_discovered:
        month_files = [
            (month, path, adapter.month_label(month)) for month, path in prem_discovered
        ]
        return month_files, "monthly", prem_discovered
    quarterly = adapter.discover_quarterly_premium_files(raw_dir, year, quarter)
    if quarterly:
        label = f"Q{quarter}"
        return [(0, p, label) for p in quarterly], "quarterly", []
    return [], "none", []


# Internal pipeline modes (unchanged): which row kinds a run keeps.
_BORDEREAU_TYPES = {"all", "premium", "claims", "outstanding"}

# IMPL-20261006-05 (addendum): the API values the frontend sends, each mapped
# to the internal mode that gives the previous behaviour. ``premium_claims``
# and ``autodetect`` both run the old ``all`` mode (every premium, paid-claims
# and outstanding sheet the file holds); there was never a separate combined
# mode. ``claims`` is the old ``claims`` mode (paid claims only).
BORDEREAU_TYPE_VALUES = ("premium", "claims", "premium_claims", "autodetect")
_API_BORDEREAU_TYPES = {
    "premium": "premium",
    "claims": "claims",
    "premium_claims": "all",
    "autodetect": "all",
}
# Old names still accepted (deprecated) -> the new name to use instead.
# ``outstanding`` has no new name: it is the old outstanding-only mode.
DEPRECATED_BORDEREAU_TYPES = {"all": "autodetect", "outstanding": None}


def _mark_secondary_audit(rec, why: str) -> None:
    """Audit line of a scanned-but-not-loaded file: kept for the trail, left out
    of the source reconciliation (sheet type 'secondary:...')."""
    rec.sheet_type = f"secondary:{rec.sheet_type}"
    rec.rows_kept = 0
    rec.notes = f"SECONDARY SOURCE — {why}; {rec.notes}".strip("; ")
    rec.parsed_totals, rec.parsed_rows, rec.footer_totals = {}, {}, {}
    rec.footer_cells = {}


def _scan_secondary_sources(adapter, raw_dir: Path, year: int, quarter: int, result,
                            *, used: set, controlling_premium: list, controlling_claims: list,
                            include_fac: bool, want_premium: bool, want_claims: bool,
                            want_outstanding: bool) -> None:
    """IMPL-20260929-02 (7): in-period files that discovery did not pick as
    controlling files (e.g. '4th Qtr. 2021 - Premium ceded - LOCAL.xls', a
    claims workbook with a premium-sounding name) are typed from content and
    compared with what the controlling files loaded. Their rows are loaded only
    when no controlling file of that type exists; duplicates are logged, never
    double-counted; new rows in a secondary file are a WARN for review."""
    for spath in adapter.in_period_files(raw_dir, year, quarter):
        if spath in used:
            continue
        try:
            paid2, ost2, exc2, aud2 = parse_claims_file(spath, include_fac=include_fac, adapter=adapter)
            prem2, exc_p2, aud_p2 = [], [], []
            if want_premium and any(a.sheet_type == "premium" and a.detected_type == "PREMIUM"
                                    for a in aud2):
                prem2, exc_p2, aud_p2 = parse_premium_file(
                    spath, "", include_fac=include_fac, adapter=adapter)
        except Exception as e:
            result.exceptions.append(ExceptionRecord(
                "WARN", "secondary_source_unreadable", spath.name,
                detail=f"in-period file not in the controlling set could not be read: {e}"))
            continue
        types = sorted({a.detected_type for a in aud2 + aud_p2 if a.detected_type})
        result.exceptions.append(ExceptionRecord(
            "INFO", "secondary_source_scanned", spath.name,
            detail=(f"file name places it in Q{quarter} {year} but discovery did not pick it as a "
                    f"controlling file; content types found: {types}")))
        loaded = set()
        for kind, st, rows, target, wanted, ctrl in (
            ("PAID", "paid", paid2, result.claims_rows, want_claims, controlling_claims),
            ("OUTSTANDING", "outstanding", ost2, result.outstanding_rows, want_outstanding,
             controlling_claims),
            ("PREMIUM", "premium", prem2, result.premium_rows, want_premium, controlling_premium),
        ):
            if not rows or not wanted:
                continue
            names = ", ".join(sorted({p.name for p in ctrl})) or "none"
            dup, new = overlap_counts(rows, target)
            if not ctrl:
                target.extend(rows)
                loaded.add(st)
                result.exceptions.append(ExceptionRecord(
                    "WARN", "content_typed_source_loaded", spath.name,
                    detail=(f"{len(rows)} {kind} rows loaded from {spath.name}: no controlling "
                            f"{kind.lower()} file by name; typed from content — please confirm")))
            elif new == 0:
                result.exceptions.append(ExceptionRecord(
                    "INFO", "duplicate_source_not_loaded", spath.name,
                    detail=(f"all {len(rows)} {kind} rows are already loaded from controlling "
                            f"file(s) {names}; not loaded (never double-count)")))
            else:
                result.exceptions.append(ExceptionRecord(
                    "WARN", "secondary_source_not_loaded", spath.name,
                    detail=(f"{new} of {len(rows)} {kind} rows are not in controlling file(s) "
                            f"{names} ({dup} duplicates); file not loaded — confirm which "
                            "file controls")))
        for rec in aud2 + aud_p2:
            if rec.sheet_type in loaded:
                continue
            _mark_secondary_audit(rec, "scanned for duplicates, not loaded")
        result.source_audit.extend(aud2 + aud_p2)
        if loaded:
            result.exceptions.extend(exc2 + exc_p2)


def _merge_blocked(items: list) -> list:
    """Sum (label, file, sheet, count) items that share label/file/sheet."""
    merged: dict = {}
    for label, fname, sheet, n in items:
        merged[(label, fname, sheet)] = merged.get((label, fname, sheet), 0) + int(n)
    return [(label, fname, sheet, n) for (label, fname, sheet), n in merged.items()]


def _row_source(row) -> str:
    return getattr(getattr(row, "audit", None), "source_filename", "") or ""


# IMPL-20261006-04 (near-duplicates): two uploaded files whose rows of one kind
# share at least this share of the LARGER file's transactions are two versions
# of the same source file (the 2021 Q4 real case: outstanding 850 shared of 855 / 858).
NEAR_DUPLICATE_MIN_SHARE = 0.95
_MTIME_TIE_SECS = 1.0
_VERSION_NUM_RE = re.compile(r"(?<![A-Z0-9])(?:V|VER|VERSION|REV|REVISION)[\s._-]*(\d{1,3})(?!\d)")
_VERSION_WORDS = ("REVISED", "UPDATED", "AMENDED", "CORRECTED", "FINAL")


def _filename_version(name: str) -> tuple:
    """(version number, revision word) a file name states, (0, 0) if none."""
    stem = Path(name).stem.upper()
    nums = [int(n) for n in _VERSION_NUM_RE.findall(stem)]
    words = re.findall(r"[A-Z]+", stem)
    return (max(nums) if nums else 0, 1 if any(w in _VERSION_WORDS for w in words) else 0)


def _newer_version(a: Path, b: Path, near: list) -> tuple:
    """(newer path or None on a tie, why). ``near`` = [(Counter a, Counter b)]
    for the kinds where the two files are near-duplicates. Order:
    1. row superset — one file holds every transaction of the other plus more
       (in every near-duplicate kind);
    2. file modification time, at least 1 s apart (the service stamps each
       download with its GCS object time, so this is the GCS time there);
    3. a version the file name states (V2 > V1, REV 3 > REV 2; REVISED /
       UPDATED / AMENDED / CORRECTED / FINAL > no such word);
    otherwise a tie (None): both stay loaded and a WARN asks which controls."""
    a_sup = all(not (cb - ca) for ca, cb in near) and any(ca != cb for ca, cb in near)
    b_sup = all(not (ca - cb) for ca, cb in near) and any(ca != cb for ca, cb in near)
    if a_sup != b_sup:
        return (a if a_sup else b), "row superset (holds every transaction of the other plus more)"
    try:
        ma, mb = a.stat().st_mtime, b.stat().st_mtime
    except OSError:
        ma = mb = 0.0
    if ma and mb and abs(ma - mb) >= _MTIME_TIE_SECS:
        newer = a if ma > mb else b
        when = datetime.fromtimestamp(max(ma, mb)).strftime("%Y-%m-%d %H:%M:%S")
        return newer, f"later file time ({when}; GCS object time for uploads)"
    va, vb = _filename_version(a.name), _filename_version(b.name)
    if va != vb:
        return (a if va > vb else b), "version named in the file name"
    return None, "tie: same rows coverage, file times within 1 s, no version in the names"


def _drop_duplicate_sources(result, inputs: Sequence[Path]) -> None:
    """Several uploaded files cleaned as one quarter (single-file / batch-group
    mode); never double-count.

    IMPL-20261006-03 (B1), unchanged: when a file's premium (or paid, or
    outstanding) rows are exactly the same multiset of transactions as an
    earlier file's rows of that kind, the later copy is not loaded (INFO
    ``duplicate_source_not_loaded``).

    IMPL-20261006-04, near-duplicates (two versions of one source file): rows
    of one kind that share >= ``NEAR_DUPLICATE_MIN_SHARE`` of the larger
    file's transactions (``transaction_key`` multiset intersection). The newer
    file (see ``_newer_version``) wins: the older file's rows of every kind the
    two share (near or exact) are not loaded and an INFO
    ``near_duplicate_source_not_loaded`` names both files. Kinds only the
    older file has are kept. On a tie nothing is dropped and a WARN
    ``near_duplicate_source_unresolved`` names both. Smaller overlaps are kept
    (the WARN ``apparent_duplicate`` review flags stay). A file with nothing
    left is marked secondary in the source audit."""
    from collections import Counter
    if len(inputs) < 2:
        return
    kinds = (("premium_rows", "premium"), ("claims_rows", "paid"),
             ("outstanding_rows", "outstanding"))
    paths: dict = {}
    for p in inputs:
        paths.setdefault(p.name, Path(p))
    order = list(paths)
    sigs: dict = {}                               # (name, attr) -> Counter
    for attr, _label in kinds:
        for name in order:
            mine = [r for r in getattr(result, attr) if _row_source(r) == name]
            if mine:
                sigs[(name, attr)] = Counter(transaction_key(r) for r in mine)

    def near(a: str, b: str, attr: str) -> Optional[int]:
        sa, sb = sigs.get((a, attr)), sigs.get((b, attr))
        if not sa or not sb or sa == sb:
            return None
        shared = sum((sa & sb).values())
        big = max(sum(sa.values()), sum(sb.values()))
        return shared if shared >= NEAR_DUPLICATE_MIN_SHARE * big else None

    verdict: dict = {}                            # (a, b) sorted -> (newer|None, why)

    def version_verdict(a: str, b: str):
        key = tuple(sorted((a, b)))
        if key not in verdict:
            pairs = [(sigs[(a, attr)], sigs[(b, attr)]) for attr, _l in kinds
                     if near(a, b, attr) is not None]
            if not pairs:
                verdict[key] = False
            else:
                newer, why = _newer_version(paths[a], paths[b], pairs)
                verdict[key] = (newer.name if newer else None, why)
        return verdict[key]

    drop: set = set()                             # (file name, kind attr)
    exact_notes: list = []
    near_notes: list = []
    unresolved: set = set()
    for attr, label in kinds:
        kept: list = []
        for name in order:
            sig = sigs.get((name, attr))
            if not sig:
                continue
            keep_me = True
            for other in list(kept):
                v = version_verdict(name, other)
                if v and v[0]:
                    newer = v[0]
                    older = other if newer == name else name
                    shared = near(name, other, attr)
                    if shared is None and sig != sigs[(other, attr)]:
                        continue                  # versions, but this kind differs a lot
                    drop.add((older, attr))
                    near_notes.append((older, newer, label, shared, sigs[(older, attr)],
                                       sigs[(newer, attr)], v[1]))
                    if older == name:
                        keep_me = False
                        break
                    kept.remove(other)
                    continue
                if v and v[0] is None and near(name, other, attr) is not None:
                    unresolved.add((tuple(sorted((name, other))), label,
                                    near(name, other, attr), v[1]))
                if sigs[(other, attr)] == sig:
                    drop.add((name, attr))
                    exact_notes.append((name, label, sum(sig.values()), other))
                    keep_me = False
                    break
            if keep_me:
                kept.append(name)
    for (a, b), label, shared, why in sorted(unresolved):
        msg = (f"{a} and {b} look like two versions of one source ({label}: {shared} "
               f"transactions shared, >= {NEAR_DUPLICATE_MIN_SHARE:.0%} of the larger); "
               f"which is newer cannot be decided ({why}); both loaded — confirm which file "
               "controls")
        result.exceptions.append(ExceptionRecord(
            "WARN", "near_duplicate_source_unresolved", a, detail=msg))
        print(f"WARN near_duplicate_source_unresolved: {msg}", file=sys.stderr)
    if not drop:
        return
    for attr, _label in kinds:
        setattr(result, attr, [r for r in getattr(result, attr)
                               if (_row_source(r), attr) not in drop])
    for name, label, n, owner in exact_notes:
        result.exceptions.append(ExceptionRecord(
            "INFO", "duplicate_source_not_loaded", name,
            detail=(f"the {n} {label} rows of {name} are the same transactions as {owner}; "
                    "not loaded again (never double-count)")))
        print(f"INFO duplicate_source_not_loaded: {name} ({label}) = {owner}", file=sys.stderr)
    for older, newer, label, shared, s_old, s_new, why in near_notes:
        n_old, n_new = sum(s_old.values()), sum(s_new.values())
        overlap = (f"{shared} of {n_old} / {n_new} transactions shared" if shared is not None
                   else f"the same {n_old} transactions")
        msg = (f"{older} and {newer} are two versions of one source file ({label}: {overlap}); "
               f"newer = {newer} by {why}; the {n_old} {label} rows of {older} are not "
               f"loaded, the {n_new} of {newer} are")
        result.exceptions.append(ExceptionRecord(
            "INFO", "near_duplicate_source_not_loaded", older, detail=msg))
        print(f"INFO near_duplicate_source_not_loaded: {msg}", file=sys.stderr)
    left = {_row_source(r) for attr, _l in kinds for r in getattr(result, attr)}
    gone = {name for name, _attr in drop} - left
    for rec in result.source_audit:
        if rec.source_filename in gone and not str(rec.sheet_type).startswith("secondary:"):
            why = ("older version of another uploaded file, not loaded"
                   if any(o == rec.source_filename for o, *_r in near_notes)
                   else "duplicate of another uploaded file, not loaded")
            _mark_secondary_audit(rec, why)


def _premium_file_month(path: Path, quarter: int) -> Optional[int]:
    """The one month a premium file name names inside ``quarter``, else None."""
    from src.domain.cre_cleaner.adapters.base import name_tokens, quarters_in_text, token_month
    stem = path.stem
    months = {m for m in (token_month(t) for t in name_tokens(stem)) if m}
    if len(months) != 1 or quarters_in_text(stem):
        return None
    month = next(iter(months))
    return month if month in QUARTER_MONTHS[quarter] else None


def _single_file_month_gaps(adapter, premium_rows, inputs: Sequence[Path], year: int,
                            quarter: int) -> List[ExceptionRecord]:
    """IMPL-20261006-03 (B18): uploaded files cleaned as one quarter still say
    when monthly premium files cover only part of it. Only files that loaded
    premium rows count; a premium file that names no single month (a
    quarterly bordereau) covers the quarter, so no warning then."""
    with_rows = {_row_source(r) for r in premium_rows}
    months: set = set()
    for path in inputs:
        if path.name not in with_rows:
            continue
        month = _premium_file_month(path, quarter)
        if month is None:
            return []
        months.add(month)
    out = []
    for month in QUARTER_MONTHS[quarter]:
        if months and month not in months:
            msg = (f"No premium file for {adapter.month_label(month)} {year} (Q{quarter}) among "
                   f"the uploaded files — month missing from the quarter")
            out.append(ExceptionRecord("WARN", "premium_month_missing", detail=msg))
            print(f"WARN premium_month_missing: {msg}", file=sys.stderr)
    return out


def normalize_bordereau_type(value: Optional[str]) -> str:
    """Internal mode (premium | claims | outstanding | all) for a request value.

    API values (IMPL-20261006-05): premium, claims, premium_claims, autodetect
    (see ``_API_BORDEREAU_TYPES``). The old names all / outstanding are still
    accepted as deprecated aliases; empty means autodetect (old default all)."""
    v = (value or "autodetect").strip().lower()
    if v in _API_BORDEREAU_TYPES:
        return _API_BORDEREAU_TYPES[v]
    if v in {"paid", "claim"}:
        return "claims"
    if v in {"ost", "os", "out"}:
        return "outstanding"
    if v in {"prem", "premiums"}:
        return "premium"
    if v not in _BORDEREAU_TYPES:
        raise ValueError(
            f"bordereau_type must be one of {list(BORDEREAU_TYPE_VALUES)} "
            f"(deprecated: {sorted(DEPRECATED_BORDEREAU_TYPES)}), got {value!r}"
        )
    return v


def field_completeness(
    premium_rows: Sequence = (),
    claims_rows: Sequence = (),
    outstanding_rows: Sequence = (),
) -> dict:
    """Share of rows with key mapped fields populated (Phase 1 QA panel)."""
    def _pct(rows, pred) -> Optional[float]:
        if not rows:
            return None
        ok = sum(1 for r in rows if pred(r))
        return round(100.0 * ok / len(rows), 1)

    def _has_num(v) -> bool:
        return v is not None and v != ""

    return {
        "premium_rows": len(premium_rows),
        "premium_policy_no_pct": _pct(premium_rows, lambda r: bool(getattr(r, "policy_no", ""))),
        "premium_gross_pct": _pct(premium_rows, lambda r: _has_num(getattr(r, "gross_premium", None))),
        "premium_retention_pct": _pct(
            premium_rows,
            lambda r: _has_num(getattr(r, "ret_prem", None)) or _has_num(getattr(r, "ret_ppn", None)),
        ),
        "premium_treaty_pct": _pct(
            premium_rows,
            lambda r: _has_num(getattr(r, "sur_prem", None)) or _has_num(getattr(r, "sur_ppn", None)),
        ),
        "claims_rows": len(claims_rows),
        "claims_policy_no_pct": _pct(claims_rows, lambda r: bool(getattr(r, "policy_no", ""))),
        "claims_total_pct": _pct(claims_rows, lambda r: _has_num(getattr(r, "total_claims", None))),
        "outstanding_rows": len(outstanding_rows),
        "outstanding_policy_no_pct": _pct(
            outstanding_rows, lambda r: bool(getattr(r, "policy_no", ""))
        ),
        "outstanding_total_pct": _pct(
            outstanding_rows, lambda r: _has_num(getattr(r, "total_claims", None))
        ),
    }


_timing_log = logging.getLogger("cre_cleaner.timing")


def _run_pipeline_impl(
    *,
    cedant: str,
    broker: str,
    year: Optional[int] = None,
    quarter: Optional[int] = None,
    raw_dir: Path,
    template: Path,
    out_dir: Path,
    base_dir: Optional[Path] = None,
    collapsed: bool = False,
    include_audit_sheets: bool = False,
    include_fac: bool = False,
    proportion_headers: str = "gold",
    out_name: Optional[str] = None,
    claims_leading_blank: bool = True,
    convert_pdfs: bool = False,
    bordereau_type: str = "all",
    single_file: Optional[bool] = None,
    extra_exceptions: Optional[list] = None,
    class_aliases: Optional[ConfirmedAliases] = None,
    ignored_labels: Optional[list] = None,
) -> PipelineResult:
    """Clean one quarter from ``raw_dir``.

    Phase 1 defaults: PDF conversion off; year/quarter inferred from sheet
    date columns when omitted; ``bordereau_type`` selects Premium / Claims /
    Outstanding / all.

    ``single_file`` (IMPL-20260929-05): True = process the uploaded file(s) in
    ``raw_dir`` as the quarter's bordereau, skipping filename quarter gates
    and discovery. None (default) = automatic: on when the period is inferred
    and ``raw_dir`` is one Excel file or holds exactly one readable Excel file.

    ``class_aliases`` (IMPL-20261006-05): the partner's class-store vocabulary
    (aliases, then variations), loaded once by the caller. It is consulted only
    for labels the built-in map leaves unresolved; its warnings (e.g.
    ``store_class_unmapped``) are written as WARN records. ``available=False`` = the store could not be read:
    WARN ``alias_store_unavailable`` and the built-in map alone. None = no
    alias layer (CLI / older callers), behaviour unchanged.
    """
    base_dir = Path(base_dir) if base_dir else Path.cwd()
    raw_dir = Path(raw_dir)
    if not raw_dir.is_absolute():
        raw_dir = (base_dir / raw_dir).resolve()
    template = Path(template)
    if not template.is_absolute():
        template = (base_dir / template).resolve()
    out_dir = Path(out_dir)
    if not out_dir.is_absolute():
        out_dir = (base_dir / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        mode = normalize_bordereau_type(bordereau_type)
    except ValueError as e:
        result = PipelineResult()
        result.exceptions.append(ExceptionRecord("ERROR", "bordereau_type_invalid", detail=str(e)))
        return result

    want_premium = mode in {"all", "premium"}
    want_claims = mode in {"all", "claims"}
    want_outstanding = mode in {"all", "outstanding"}

    adapter = get_adapter(cedant, broker)
    class_map = adapter.class_map() if hasattr(adapter, "class_map") else None
    result = PipelineResult()
    if include_fac:
        # IMPL-20261006-03 (B3): Facultative is never its own output class
        # (Continental / Bisola). The flag is accepted for compatibility and
        # ignored: FAC OBLIG tabs are skipped and FAC-class rows left out.
        result.exceptions.append(ExceptionRecord(
            "INFO", "include_fac_ignored",
            detail=("include_fac=True ignored: Facultative is never written as its own "
                    "class sheet; FAC shares stay in each row's FAC columns"),
        ))
        include_fac = False
    result.exceptions.append(ExceptionRecord(
        "INFO", "adapter_status",
        detail=f"{cedant.upper()}/{broker.upper()}: {adapter.status_text()}",
    ))
    result.exceptions.append(ExceptionRecord(
        "INFO", "bordereau_type",
        detail=f"Phase 1 mode={mode}",
    ))
    # Caller findings about the inputs (e.g. batch WARN cedant_mismatch_suspected)
    # go into this run's exceptions, sidecar and summary.
    for rec in extra_exceptions or []:
        result.exceptions.append(rec)
        print(f"{rec.severity} {rec.reason}: {rec.detail}", file=sys.stderr)
    if class_aliases is not None:
        if not class_aliases.available:
            msg = (
                "Confirmed class aliases could not be loaded; using the built-in class "
                "map only (labels it does not resolve stay unresolved, nothing is "
                f"auto-mapped). {class_aliases.detail}"
            ).strip()
            result.exceptions.append(ExceptionRecord("WARN", ALIAS_STORE_UNAVAILABLE, detail=msg))
            print(f"WARN {ALIAS_STORE_UNAVAILABLE}: {msg}", file=sys.stderr)
        else:
            for warn in class_aliases.warnings:
                code = str(warn.get("code") or "store_warning")
                text = str(warn.get("message") or "")
                result.exceptions.append(ExceptionRecord("WARN", code, detail=text))
                print(f"WARN {code}: {text}", file=sys.stderr)
            class_map = with_confirmed_aliases(class_map, class_aliases)
            tiers = class_aliases.counts()
            result.exceptions.append(ExceptionRecord(
                "INFO", CLASS_ALIASES_LOADED,
                detail=(f"partner class store: {tiers.get('alias', 0)} alias label(s) and "
                        f"{tiers.get('variation', 0)} variation label(s) apply to this run "
                        "(after the built-in map; keywords are never used)"),
            ))

    # --- PDF → Excel (paused for Phase 1 unless explicitly enabled) ---
    if convert_pdfs and list_pdfs(raw_dir):
        batch = convert_pdfs_in_dir(raw_dir)
        for conv in batch.conversions:
            sev = "INFO" if conv.ok or conv.skipped else "ERROR"
            result.exceptions.append(ExceptionRecord(
                sev, "pdf_convert",
                source_filename=conv.source.name,
                detail=conv.detail or ("ok" if conv.ok else "failed"),
            ))
            if sev == "ERROR":
                print(f"ERROR pdf_convert {conv.source.name}: {conv.detail}", file=sys.stderr)
    elif list_pdfs(raw_dir) and not convert_pdfs:
        result.exceptions.append(ExceptionRecord(
            "INFO", "pdf_skipped_phase1",
            detail=(
                f"{len(list_pdfs(raw_dir))} PDF file(s) present but ignored "
                "(Phase 1: Excel only; set convert_pdfs=True to enable LlamaParse)"
            ),
        ))

    # --- Year / quarter (explicit or inferred from date columns / filenames) ---
    period_inferred = year is None or quarter is None
    excel_inputs = _iter_excel_paths(raw_dir)
    if single_file is None:
        single_file = period_inferred and (raw_dir.is_file() or len(excel_inputs) == 1)
    single_file = bool(single_file)
    if period_inferred:
        # A bare year folder anywhere in the input path counts (IMPL-20261006-04).
        inferred = infer_period(raw_dir, folder_year=folder_year(raw_dir))
        if inferred.filename_override:
            result.exceptions.append(ExceptionRecord(
                "WARN", "period_filename_override", detail=inferred.filename_override,
            ))
            print(f"WARN period_filename_override: {inferred.filename_override}", file=sys.stderr)
        # Banner / row-date / file-name combination notes (IMPL-20260929-08):
        # INFO period_banner_stale, WARN period_banner_conflict.
        for sev, reason, detail in inferred.notes:
            result.exceptions.append(ExceptionRecord(sev, reason, detail=detail))
            if sev != "INFO":
                print(f"{sev} {reason}: {detail}", file=sys.stderr)
        for w in inferred.warnings:
            result.exceptions.append(ExceptionRecord(
                "WARN", "period_infer", detail=w,
            ))
        if inferred.evidence:
            result.exceptions.append(ExceptionRecord(
                "INFO", "period_infer_evidence",
                detail=f"confidence={inferred.confidence}; " + "; ".join(inferred.evidence[:8]),
            ))
        if year is None:
            year = inferred.year
        if quarter is None:
            quarter = inferred.quarter
        if year is None or quarter is None:
            msg = (
                f"Could not settle the reporting period (year={year}, quarter="
                f"{quarter}): no report banner (Q-label / From…To / As At), no "
                "decisive date columns, and the file name does not name one "
                "clear quarter and year. Pick the year and quarter explicitly."
            )
            result.exceptions.append(ExceptionRecord("ERROR", "period_ambiguous", detail=msg))
            result.exceptions.append(ExceptionRecord("ERROR", "period_unresolved", detail=msg))
            print(f"ERROR period_ambiguous: {msg}", file=sys.stderr)
            result.summary = {
                "cedant": cedant, "broker": broker,
                "year": year, "quarter": quarter,
                "adapter_status": adapter.status_text(),
                "bordereau_type": mode,
                "error": "period_ambiguous",
            }
            return result

    year = int(year)
    quarter = int(quarter)
    if quarter not in (1, 2, 3, 4):
        result.exceptions.append(ExceptionRecord(
            "ERROR", "period_invalid", detail=f"quarter must be 1–4, got {quarter}",
        ))
        return result
    # Adapter settings may be overridden per year / quarter.
    # (values used are logged per sheet in the source-audit notes).
    if hasattr(adapter, "set_period"):
        adapter.set_period(year, quarter)

    single_inputs: list = []
    if single_file:
        single_inputs = list(excel_inputs)
        result.exceptions.append(ExceptionRecord(
            "INFO", "single_file_mode",
            detail=(
                f"Single-file mode: processing {[p.name for p in single_inputs]} as "
                f"Q{quarter} {year} (filename quarter gates and folder discovery skipped)"
            ),
        ))
        if raw_dir.is_file():
            raw_dir = raw_dir.parent

    # --- Premium ---
    month_files: list = []
    prem_mode = "none"
    prem_discovered: list = []
    if want_premium:
        if single_file:
            month_files = [(0, p, f"Q{quarter}") for p in single_inputs]
            prem_mode = "quarterly" if month_files else "none"
        else:
            month_files, prem_mode, prem_discovered = _discover_premium_inputs(
                adapter, raw_dir, year, quarter,
            )
        if prem_mode == "none":
            result.exceptions.append(ExceptionRecord(
                "ERROR", "no_premium_files",
                detail=f"No monthly or quarterly premium files for Q{quarter} {year} in {raw_dir}",
            ))
        elif prem_mode == "monthly":
            found_months = {m for m, _ in prem_discovered}
            for month in QUARTER_MONTHS[quarter]:
                if month not in found_months:
                    # Phase 1: one monthly file still yields a Qn workbook; missing
                    # months are WARN (multi-file merge remains available later).
                    msg = (
                        f"No premium file found for {adapter.month_label(month)} {year} "
                        f"(Q{quarter}) in {raw_dir} — month missing from the quarter"
                    )
                    result.exceptions.append(ExceptionRecord(
                        "WARN", "premium_month_missing", detail=msg,
                    ))
                    print(f"WARN premium_month_missing: {msg}", file=sys.stderr)
        else:
            result.exceptions.append(ExceptionRecord(
                "INFO", "premium_input_quarterly",
                detail=(
                    f"No monthly premium files for Q{quarter} {year}; using "
                    f"{len(month_files)} quarterly premium file(s): "
                    f"{[p.name for _, p, _ in month_files]}"
                ),
            ))

        for sev, reason, fname, detail in getattr(adapter, "discovery_notes", []) or []:
            result.exceptions.append(ExceptionRecord(sev, reason, fname, detail=detail))
            print(f"{sev} {reason}: {detail}", file=sys.stderr)

        premium_rows, exc_p, audit_p = merge_monthly_premiums(
            month_files, include_fac=include_fac, adapter=adapter,
        )
        result.premium_rows = premium_rows
        result.exceptions.extend(exc_p)
        result.source_audit.extend(audit_p)

    # --- Claims / outstanding ---
    claims_files: list = []
    if want_claims or want_outstanding:
        claims_files = (list(single_inputs) if single_file
                        else adapter.discover_claims_files(raw_dir, year, quarter))
        # Guard: a quarter never loads another quarter's claims file, whatever
        # the adapter's discovery matched (IMPL-20260929-02 / -03).
        in_period = []
        for cpath in claims_files:
            why = None if single_file else claims_file_period_conflict(cpath.name, quarter)
            if why:
                result.exceptions.append(ExceptionRecord(
                    "ERROR", "claims_file_out_of_period", cpath.name,
                    detail=f"not loaded for Q{quarter} {year}: {why}"))
                print(f"ERROR claims_file_out_of_period: {cpath.name}: {why}", file=sys.stderr)
            else:
                in_period.append(cpath)
        claims_files = in_period
        if not claims_files:
            msg = (
                f"No claims / outstanding files found for Q{quarter} {year} in {raw_dir} "
                "(looked for CLAIM/LOSS/OUTSTANDING + quarter or month in the filename)"
            )
            result.exceptions.append(ExceptionRecord(
                "ERROR", "no_claims_files", detail=msg,
            ))
            print(f"ERROR no_claims_files: {msg}", file=sys.stderr)
        paid_all = []
        ost_all = []
        for cpath in claims_files:
            try:
                paid, ost, exc_c, audit_c = parse_claims_file(
                    cpath, include_fac=include_fac, adapter=adapter,
                )
                if want_claims:
                    paid_all.extend(paid)
                if want_outstanding:
                    ost_all.extend(ost)
                result.exceptions.extend(exc_c)
                result.source_audit.extend(audit_c)
            except Exception as e:
                result.exceptions.append(ExceptionRecord(
                    "ERROR", "claims_parse_failed",
                    source_filename=cpath.name,
                    detail=str(e),
                ))

        result.claims_rows = paid_all
        result.outstanding_rows = ost_all

    if single_file and len(single_inputs) > 1:
        _drop_duplicate_sources(result, single_inputs)
    if single_file and want_premium:
        result.exceptions.extend(_single_file_month_gaps(
            adapter, result.premium_rows, single_inputs, year, quarter,
        ))

    # An inferred period that discovery filters to nothing, while the folder
    # holds Excel files, is a wrong guess — never an empty clean.
    if period_inferred and not single_file and not month_files and not claims_files and excel_inputs:
        msg = (
            f"Inferred period Q{quarter} {year} matched none of the "
            f"{len(excel_inputs)} Excel file(s) in {raw_dir} "
            f"({', '.join(p.name for p in excel_inputs[:5])}). Pick the year and "
            "quarter explicitly or upload the files one at a time."
        )
        result.exceptions.append(ExceptionRecord("ERROR", "period_discovery_empty", detail=msg))
        print(f"ERROR period_discovery_empty: {msg}", file=sys.stderr)
        result.summary = {
            "cedant": cedant, "broker": broker, "year": year, "quarter": quarter,
            "adapter_status": adapter.status_text(), "bordereau_type": mode,
            "error": "period_discovery_empty",
        }
        return result

    if getattr(adapter, "content_sheet_typing", False) and not single_file:
        _scan_secondary_sources(
            adapter, raw_dir, year, quarter, result,
            used={p for _m, p, _l in month_files} | set(claims_files),
            controlling_premium=[p for _m, p, _l in month_files],
            controlling_claims=list(claims_files),
            include_fac=include_fac, want_premium=want_premium,
            want_claims=want_claims, want_outstanding=want_outstanding,
        )

    if not include_fac:
        result.premium_rows, result.claims_rows, result.outstanding_rows = _drop_fac_rows(
            result.premium_rows, result.claims_rows, result.outstanding_rows, class_map,
        )

    diverted_blocked: list = []
    for attr, getter, label in (
        ("premium_rows", premium_class_hint, "PREMIUM"),
        ("claims_rows", claims_class_hint, "CLAIMS"),
        ("outstanding_rows", claims_class_hint, "OUTSTANDING"),
    ):
        kept, excs = _divert_unresolved_class(
            getattr(result, attr), getter, label, blocked=diverted_blocked,
            class_map=class_map,
        )
        setattr(result, attr, kept)
        result.exceptions.extend(excs)

    result.exceptions.extend(flag_duplicate_premium(result.premium_rows))
    result.exceptions.extend(flag_duplicate_claims(result.claims_rows, "CLAIMS BORDEREAU"))
    result.exceptions.extend(
        flag_duplicate_claims(result.outstanding_rows, "OUTSTANDING LOSS BORDEREAU")
    )

    split_exc, split_counts = check_row_splits(
        result.premium_rows, result.claims_rows, result.outstanding_rows,
    )
    result.exceptions.extend(split_exc)
    date_exc, date_counts = check_row_dates(
        result.premium_rows, result.claims_rows, result.outstanding_rows,
        year=year, quarter=quarter,
    )
    result.exceptions.extend(date_exc)
    # Manual Table 13: printed totals are a separate comparison (INFO/WARN).
    result.exceptions.extend(printed_total_variances(result.source_audit))

    layout_note = (
        "collapsed single PREMIUM/CLAIMS/OUTSTANDING sheets"
        if collapsed
        else "class-split sheets ({Class} - PREMIUM|CLAIMS|OUTSTANDING) like Bisola"
    )
    fac_note = (
        "Facultative sheets included (--include-fac)."
        if include_fac
        else "Facultative source sheets ignored (not a treaty class)."
    )
    audit_note = (
        "EXCEPTIONS/SOURCE AUDIT embedded in workbook."
        if include_audit_sheets
        else "EXCEPTIONS/SOURCE AUDIT written as sidecar files (not in upload workbook)."
    )
    prem_note = (
        "Premium input: monthly files."
        if prem_mode == "monthly"
        else (
            "Premium input: quarterly file(s) (no monthly files found)."
            if prem_mode == "quarterly"
            else "Premium input: none found."
        )
    )
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S WAT")
    notes = (
        f"Default layout: {layout_note}. "
        f"{fac_note} {audit_note} {prem_note} "
        "Paid vs Outstanding kept separate; negatives preserved; "
        "upload headers use TREATY (not SURPLUS) with unique RET/TREATY/FAC "
        "amount and premium designations. "
        f"Adapter: {adapter.status_text()}."
    )

    blocked = (
        unapproved_class_rows(result.premium_rows, premium_class_hint, class_map)
        + unapproved_class_rows(result.claims_rows, claims_class_hint, class_map)
        + unapproved_class_rows(result.outstanding_rows, claims_class_hint, class_map)
        + _merge_blocked(diverted_blocked)
    )
    blocked, ignored_entries = _partition_ignored(blocked, ignored_labels)
    if ignored_entries:
        # Caller chose ignore: drop matching rows from the workbook and record them.
        result.premium_rows, n1 = _drop_ignored_rows(
            result.premium_rows, premium_class_hint, ignored_labels)
        result.claims_rows, n2 = _drop_ignored_rows(
            result.claims_rows, claims_class_hint, ignored_labels)
        result.outstanding_rows, n3 = _drop_ignored_rows(
            result.outstanding_rows, claims_class_hint, ignored_labels)
        result.summary["ignored"] = ignored_entries
        result.exceptions.append(ExceptionRecord(
            "INFO", "class_labels_ignored",
            detail=(f"{len(ignored_entries)} label(s) ignored by request "
                    f"({n1 + n2 + n3} row(s) excluded from the cleaned workbook)"),
        ))
    if blocked:
        detail = format_unapproved_classes(blocked)
        result.exceptions.append(ExceptionRecord(
            "ERROR", "class_unresolved", detail=detail,
        ))
        result.summary = {
            "cedant": cedant.upper(),
            "broker": broker.upper(),
            "year": year,
            "quarter": quarter,
            "error": "class_unresolved",
            "detail": detail,
            # Advisory suggestions only: nothing is applied, the run still fails.
            "unresolved": unresolved_class_entries(
                blocked, class_map, cedant=cedant.upper(), broker=broker.upper(),
            ),
        }
        return result

    for label, cls, n in alias_applications((
        (result.premium_rows, premium_class_hint),
        (result.claims_rows, claims_class_hint),
        (result.outstanding_rows, claims_class_hint),
    ), class_map):
        result.exceptions.append(ExceptionRecord(
            "INFO", CLASS_ALIAS_APPLIED,
            detail=f"{label!r} -> {cls} by the partner class store ({n} record(s))",
        ))

    currency_groups = _split_by_currency(
        result.premium_rows, result.claims_rows, result.outstanding_rows,
    )
    if len(currency_groups) > 1:
        result.exceptions.append(ExceptionRecord(
            "INFO", "currency_split_workbooks",
            detail=(
                f"Source has {len(currency_groups)} currencies "
                f"{[c for c, *_ in currency_groups]}; writing one cleaned workbook each "
                "(amounts never summed across currencies)"
            ),
        ))

    primary: Optional[PipelineResult] = None
    for ccy, prem_c, paid_c, ost_c in currency_groups:
        by_class = _by_class_counts(prem_c, paid_c, ost_c, class_map)
        recon = build_source_reconciliation(
            result.source_audit, prem_c, paid_c, ost_c,
            currency=ccy, year=year, quarter=quarter,
        )
        summary = build_summary(
            cedant=cedant.upper(),
            broker=broker.upper(),
            year=year,
            quarter=quarter,
            generated_at=generated_at,
            premium_files=[p.name for _, p, _ in month_files],
            claims_files=[p.name for p in claims_files],
            premium_rows=prem_c,
            claims_rows=paid_c,
            outstanding_rows=ost_c,
            exceptions=result.exceptions,
            notes=notes,
        )
        summary["by_class"] = by_class
        summary["output_layout"] = "collapsed" if collapsed else "class-split"
        summary["currency"] = ccy
        summary["adapter_status"] = adapter.status_text()
        summary["split_checks"] = split_counts
        summary["date_checks"] = date_counts
        summary["reconciliation"] = recon
        summary["premium_input_mode"] = prem_mode
        summary["bordereau_type"] = mode
        summary["field_completeness"] = field_completeness(prem_c, paid_c, ost_c)
        if ignored_entries:
            summary["ignored"] = ignored_entries

        if out_name and len(currency_groups) == 1:
            name = out_name if out_name.lower().endswith(".xlsx") else out_name + ".xlsx"
        elif len(currency_groups) == 1 and ccy == "NGN":
            name = f"{cedant.upper()}_{broker.upper()}_{year}_Q{quarter}_cleaned.xlsx"
        else:
            name = f"{cedant.upper()}_{broker.upper()}_{year}_Q{quarter}_{ccy}_cleaned.xlsx"

        out_path = out_dir / name
        ccy_exc = _filter_exceptions_for_currency(result.exceptions, ccy, result.source_audit)
        ccy_audit = _filter_audit_for_currency(result.source_audit, ccy)
        cleaned, exc_path, audit_path = write_output_workbook(
            template_path=template,
            output_path=out_path,
            premium_rows=prem_c,
            claims_rows=paid_c,
            outstanding_rows=ost_c,
            exceptions=ccy_exc,
            source_audit=ccy_audit,
            summary=summary,
            collapsed=collapsed,
            include_audit_sheets=include_audit_sheets,
            include_fac=include_fac,
            proportion_mode=proportion_headers,
            claims_leading_blank=claims_leading_blank,
            class_map=class_map,
        )
        writer_exc = getattr(write_output_workbook, "last_writer_exceptions", []) or []
        result.exceptions.extend(writer_exc)
        entry = {
            "currency": ccy,
            "output_path": str(cleaned),
            "exceptions_path": str(exc_path),
            "source_audit_path": str(audit_path),
            "summary": summary,
            "premium_rows": len(prem_c),
            "claims_rows": len(paid_c),
            "outstanding_rows": len(ost_c),
        }
        result.outputs.append(entry)
        if primary is None or ccy == "NGN":
            result.output_path = str(cleaned)
            result.exceptions_path = str(exc_path)
            result.source_audit_path = str(audit_path)
            result.summary = summary
            # Keep row lists as the primary currency's rows for demo metrics.
            if ccy == "NGN" or primary is None:
                result.premium_rows = prem_c
                result.claims_rows = paid_c
                result.outstanding_rows = ost_c
                primary = result

    if not result.outputs:
        # No rows at all — still write an empty NGN workbook so callers get paths.
        summary = build_summary(
            cedant=cedant.upper(), broker=broker.upper(), year=year, quarter=quarter,
            generated_at=generated_at,
            premium_files=[p.name for _, p, _ in month_files],
            claims_files=[p.name for p in claims_files],
            premium_rows=[], claims_rows=[], outstanding_rows=[],
            exceptions=result.exceptions, notes=notes,
        )
        summary["currency"] = "NGN"
        summary["adapter_status"] = adapter.status_text()
        summary["split_checks"] = split_counts
        summary["date_checks"] = date_counts
        summary["reconciliation"] = []
        summary["premium_input_mode"] = prem_mode
        summary["bordereau_type"] = mode
        summary["field_completeness"] = field_completeness([], [], [])
        summary["by_class"] = {}
        if ignored_entries:
            summary["ignored"] = ignored_entries
        name = out_name or f"{cedant.upper()}_{broker.upper()}_{year}_Q{quarter}_cleaned.xlsx"
        if not name.lower().endswith(".xlsx"):
            name += ".xlsx"
        cleaned, ec, au = write_output_workbook(
            template_path=template, output_path=out_dir / name,
            premium_rows=[], claims_rows=[], outstanding_rows=[],
            exceptions=result.exceptions, source_audit=result.source_audit,
            summary=summary, collapsed=collapsed,
            include_audit_sheets=include_audit_sheets, include_fac=include_fac,
            proportion_mode=proportion_headers, claims_leading_blank=claims_leading_blank,
            class_map=class_map,
        )
        result.output_path = str(cleaned)
        result.exceptions_path = str(ec)
        result.source_audit_path = str(au)
        result.summary = summary
        result.outputs.append({
            "currency": "NGN", "output_path": str(cleaned),
            "exceptions_path": str(ec), "source_audit_path": str(au),
            "summary": summary, "premium_rows": 0, "claims_rows": 0, "outstanding_rows": 0,
        })

    return result


@functools.wraps(_run_pipeline_impl)
def run_pipeline(**kwargs) -> PipelineResult:
    """``_run_pipeline_impl`` inside a workbook read cache: each source workbook
    is parsed once per run instead of once per stage (IMPL-20261002-01)."""
    t0 = time.perf_counter()
    with workbook_read_cache():
        result = _run_pipeline_impl(**kwargs)
    secs = time.perf_counter() - t0
    _timing_log.info(
        "cre_pipeline_timing cedant=%s broker=%s type=%s secs=%.2f",
        kwargs.get("cedant"), kwargs.get("broker"), kwargs.get("bordereau_type", "all"),
        secs,
    )
    log_timing(
        "cre_pipeline_timing",
        cedant=kwargs.get("cedant"), broker=kwargs.get("broker"),
        bordereau_type=kwargs.get("bordereau_type", "all"),
        single_file=bool(kwargs.get("single_file")), secs=round(secs, 3),
    )
    return result
