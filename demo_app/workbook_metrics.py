"""Sheet-derived totals for compare (do not trust SUMMARY sheets)."""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from cre_cleaner.core.filters import looks_like_total_row
from cre_cleaner.io.excel import read_workbook_sheets as _read_workbook_sheets
from cre_cleaner.io.excel import sheet_names as _sheet_names
from cre_cleaner.core.normalize import as_text_id, clean_text, normalize_header, parse_date, parse_number


SKIP_SHEETS = {"SUMMARY", "SOURCE AUDIT", "EXCEPTIONS"}

# kind → frozenset of row fingerprints (for month-scoped gold subsetting)
FingerprintScope = Dict[str, Set[Tuple[str, ...]]]


def read_workbook_sheets(path: Path) -> Dict[str, List[List[Any]]]:
    """Read sheets; silence openpyxl noise from mis-typed sum-insured columns."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Cell .* is marked as a date but the serial value",
            category=UserWarning,
        )
        return _read_workbook_sheets(path)


def sheet_names(path: Path) -> List[str]:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Cell .* is marked as a date but the serial value",
            category=UserWarning,
        )
        return _sheet_names(path)


def unique_headers(headers: List[str]) -> List[str]:
    """Make header labels unique for pandas (PROPORTION % repeats per band)."""
    seen: Dict[str, int] = {}
    out: List[str] = []
    for h in headers:
        base = h or "Col"
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(base if n == 0 else f"{base} ({n + 1})")
    return out


@dataclass
class BucketTotals:
    rows: int = 0
    amount: float = 0.0
    sheets: List[str] = field(default_factory=list)


@dataclass
class WorkbookMetrics:
    path: str
    sheet_names: List[str]
    data_sheets: List[str]
    premium: BucketTotals
    paid: BucketTotals
    outstanding: BucketTotals
    # Sheets not counted in any bucket, as "name — reason" (shown on Compare).
    skipped_sheets: List[str] = field(default_factory=list)
    scope_note: str = ""  # e.g. month-scoped subset of quarterly gold


def _is_fac_sheet(name: str) -> bool:
    n = clean_text(name).upper()
    return "FACULTATIVE" in n or n.strip().startswith("FAC ")


def _classify_sheet(name: str) -> Optional[str]:
    """Return premium | paid | outstanding, or None to skip."""
    n = clean_text(name).upper()
    if not n or n in SKIP_SHEETS:
        return None
    is_ost = (
        "OUTS" in n
        or "OUTSAND" in n
        or n.rstrip().endswith(" OS")
        or " - OS" in n
    )
    is_claim = "CLAI" in n
    if "PREM" in n and not is_claim and not is_ost:
        return "premium"
    if is_ost:
        return "outstanding"
    if is_claim:
        return "paid"
    return None


def _find_header_row(rows: List[List[Any]], kind: str) -> Optional[int]:
    """Locate header row by looking for key column labels."""
    needles = {
        "premium": ("GROSS PREMIUM", "POLICY NO", "NAME OF INSURED"),
        "paid": ("TOTAL CLAIMS", "CLAIM NO", "INSURED"),
        "outstanding": ("TOTAL CLAIMS", "CLAIM NO", "INSURED"),
    }[kind]
    limit = min(len(rows), 12)
    best_i = None
    best_score = 0
    for i in range(limit):
        headers = [normalize_header(c) for c in rows[i]]
        joined = " | ".join(headers)
        score = sum(1 for n in needles if n in joined)
        if score > best_score:
            best_score = score
            best_i = i
    return best_i if best_score >= 2 else best_i


def _col_index(headers: List[Any], *needles: str) -> Optional[int]:
    norms = [normalize_header(h) for h in headers]
    for needle in needles:
        for i, h in enumerate(norms):
            if h == needle or needle in h:
                return i
    return None


def _amount_col_index(headers: List[Any], kind: str) -> Optional[int]:
    if kind == "premium":
        return _col_index(headers, "GROSS PREMIUM")
    return _col_index(headers, "TOTAL CLAIMS")


def _id_cols(headers: List[Any], kind: str) -> Tuple[Optional[int], Optional[int]]:
    """Return (insured_or_name_col, policy_col) for footer detection."""
    if kind == "premium":
        return (
            _col_index(headers, "NAME OF INSURED"),
            _col_index(headers, "POLICY NO"),
        )
    return (
        _col_index(headers, "INSURED"),
        _col_index(headers, "POLICY NO"),
    )


def _fp_atom(val: Any) -> str:
    d = parse_date(val)
    if d is not None:
        return d.strftime("%Y-%m-%d")
    n = parse_number(val)
    if n is not None:
        return f"{n:.2f}"
    return as_text_id(val).upper()


def _row_fingerprint(
    kind: str, headers: List[Any], row: List[Any],
) -> Optional[Tuple[str, ...]]:
    """Stable identity for matching a cleaned row to the same row inside quarterly gold.

    Policy + party + amount + period (or loss date) — not inception-month alone,
    because a monthly bordereau is a *submission*, not \"FROM ∈ that month\".
    """
    if kind == "premium":
        policy_i = _col_index(headers, "POLICY NO")
        insured_i = _col_index(headers, "NAME OF INSURED")
        amount_i = _col_index(headers, "GROSS PREMIUM")
        from_i = _col_index(headers, "FROM")
        to_i = _col_index(headers, "TO")
        claim_i = None
        loss_i = None
    else:
        policy_i = _col_index(headers, "POLICY NO")
        insured_i = _col_index(headers, "INSURED")
        amount_i = _col_index(headers, "TOTAL CLAIMS")
        from_i = _col_index(headers, "FROM")
        to_i = _col_index(headers, "TO")
        claim_i = _col_index(headers, "CLAIM NO")
        loss_i = _col_index(headers, "DATE OF LOSS")

    def cell(i: Optional[int]) -> Any:
        if i is None or i >= len(row):
            return None
        return row[i]

    policy = _fp_atom(cell(policy_i))
    if not policy:
        return None
    parts = [
        kind,
        policy,
        _fp_atom(cell(insured_i)),
        _fp_atom(cell(amount_i)),
        _fp_atom(cell(from_i)),
        _fp_atom(cell(to_i)),
    ]
    if claim_i is not None:
        parts.append(_fp_atom(cell(claim_i)))
    if loss_i is not None:
        parts.append(_fp_atom(cell(loss_i)))
    return tuple(parts)


def _is_skip_row(
    row: List[Any],
    *,
    kind: str,
    headers: List[Any],
    amount_i: Optional[int],
) -> bool:
    texts = [clean_text(c).upper() for c in row if c is not None and clean_text(c)]
    if not texts:
        return True
    insured_i, policy_i = _id_cols(headers, kind)
    if looks_like_total_row(row, [insured_i, policy_i]):
        return True
    insured = clean_text(row[insured_i]) if insured_i is not None and insured_i < len(row) else ""
    policy = clean_text(row[policy_i]) if policy_i is not None and policy_i < len(row) else ""
    if not insured and not policy:
        return True
    non_empty = sum(1 for c in row if c is not None and clean_text(c))
    if non_empty < 2:
        return True
    return False


def _iter_data_rows(rows: List[List[Any]], kind: str):
    """Yield (headers, row, amount_i) for real transaction rows on a sheet."""
    hdr_i = _find_header_row(rows, kind)
    if hdr_i is None:
        return
    headers = rows[hdr_i]
    amount_i = _amount_col_index(headers, kind)
    for row in rows[hdr_i + 1 :]:
        if _is_skip_row(row, kind=kind, headers=headers, amount_i=amount_i):
            continue
        yield headers, row, amount_i


def collect_fingerprints(path: Path, *, exclude_fac: bool = True) -> FingerprintScope:
    """Fingerprints of every counted row in a cleaned workbook, by bordereau kind."""
    path = Path(path)
    sheets = read_workbook_sheets(path)
    out: FingerprintScope = {"premium": set(), "paid": set(), "outstanding": set()}
    for sn in sheet_names(path):
        kind = _classify_sheet(sn)
        if kind is None:
            continue
        if exclude_fac and _is_fac_sheet(sn):
            continue
        for headers, row, _amount_i in _iter_data_rows(sheets.get(sn) or [], kind):
            fp = _row_fingerprint(kind, headers, row)
            if fp is not None:
                out[kind].add(fp)
    return out


def _sheet_row_amount(
    rows: List[List[Any]],
    kind: str,
    *,
    allow_fingerprints: Optional[Set[Tuple[str, ...]]] = None,
) -> Tuple[int, float]:
    """Count rows/amount. If ``allow_fingerprints`` is a set (including empty),
    only rows whose fingerprint is in that set are counted — empty set ⇒ 0."""
    n = 0
    total = 0.0
    for headers, row, amount_i in _iter_data_rows(rows, kind):
        if allow_fingerprints is not None:
            fp = _row_fingerprint(kind, headers, row)
            if fp is None or fp not in allow_fingerprints:
                continue
        n += 1
        if amount_i is not None and amount_i < len(row):
            v = parse_number(row[amount_i])
            if v is not None:
                total += v
    return n, total


def compute_workbook_metrics(
    path: Path,
    *,
    exclude_fac: bool = True,
    fingerprint_scope: Optional[FingerprintScope] = None,
    scope_note: str = "",
) -> WorkbookMetrics:
    """Sheet totals. Pass ``fingerprint_scope`` to count only matching gold rows
    (month-scoped compare against a quarterly Bisola workbook)."""
    path = Path(path)
    names = sheet_names(path)
    sheets = read_workbook_sheets(path)

    premium = BucketTotals()
    paid = BucketTotals()
    outstanding = BucketTotals()
    data_sheets: List[str] = []
    skipped: List[str] = []

    for sn in names:
        kind = _classify_sheet(sn)
        if kind is None:
            if clean_text(sn).upper() not in SKIP_SHEETS:
                skipped.append(f"{sn} — not recognised as premium/claims/outstanding")
            continue
        if exclude_fac and _is_fac_sheet(sn):
            skipped.append(f"{sn} — Facultative (excluded)")
            continue
        rows = sheets.get(sn) or []
        allow = None if fingerprint_scope is None else fingerprint_scope.get(kind, set())
        # Scoped compare: empty fingerprint set for a kind means that bordereau
        # was not in the clean (e.g. premium-only upload) — skip those gold sheets.
        if allow is not None and len(allow) == 0:
            continue
        count, amount = _sheet_row_amount(rows, kind, allow_fingerprints=allow)
        if allow is not None and count == 0:
            continue
        data_sheets.append(sn)
        bucket = {"premium": premium, "paid": paid, "outstanding": outstanding}[kind]
        bucket.rows += count
        bucket.amount += amount
        bucket.sheets.append(sn)

    return WorkbookMetrics(
        path=str(path),
        sheet_names=list(names),
        data_sheets=data_sheets,
        premium=premium,
        paid=paid,
        outstanding=outstanding,
        skipped_sheets=skipped,
        scope_note=scope_note,
    )


def metrics_for_compare(
    ours_path: Path,
    gold_path: Path,
    *,
    coverage: Optional[dict] = None,
    exclude_fac: bool = True,
) -> Tuple[WorkbookMetrics, WorkbookMetrics]:
    """Ours always full-file. Gold is subset-matched when coverage is a single month."""
    ours_path = Path(ours_path)
    gold_path = Path(gold_path)
    coverage = coverage or {}
    ours = compute_workbook_metrics(ours_path, exclude_fac=exclude_fac)

    if coverage.get("kind") == "single_month":
        fps = collect_fingerprints(ours_path, exclude_fac=exclude_fac)
        label = coverage.get("month_label") or "month"
        note = (
            f"Gold scoped to {label} — rows matching this single-file clean "
            f"inside the quarterly Bisola workbook"
        )
        gold = compute_workbook_metrics(
            gold_path,
            exclude_fac=exclude_fac,
            fingerprint_scope=fps,
            scope_note=note,
        )
        return ours, gold

    gold = compute_workbook_metrics(gold_path, exclude_fac=exclude_fac)
    return ours, gold


def amounts_match(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) <= tol


def compare_metrics(
    ours: WorkbookMetrics, gold: WorkbookMetrics
) -> Dict[str, Dict[str, Any]]:
    """Headline compare dict for the UI."""
    out: Dict[str, Dict[str, Any]] = {}
    pairs = [
        ("Premium", ours.premium, gold.premium),
        ("Paid claims", ours.paid, gold.paid),
        ("Outstanding claims", ours.outstanding, gold.outstanding),
    ]
    for label, o, g in pairs:
        rows_ok = o.rows == g.rows
        amt_ok = amounts_match(o.amount, g.amount)
        out[label] = {
            "ours_rows": o.rows,
            "gold_rows": g.rows,
            "ours_amount": o.amount,
            "gold_amount": g.amount,
            "rows_match": rows_ok,
            "amount_match": amt_ok,
            "match": rows_ok and amt_ok,
        }
    return out


def preview_sheet(
    path: Path, sheet_name: str, max_rows: int = 20
) -> Tuple[List[str], List[List[Any]]]:
    """Return (headers, data rows) for Review screen."""
    sheets = read_workbook_sheets(Path(path))
    rows = sheets.get(sheet_name) or []
    kind = _classify_sheet(sheet_name) or "paid"
    hdr_i = _find_header_row(rows, kind if kind != "outstanding" else "outstanding")
    if hdr_i is None:
        for i, row in enumerate(rows[:8]):
            if any(c is not None and clean_text(c) for c in row):
                hdr_i = i
                break
    if hdr_i is None:
        return [], []
    headers_raw = [clean_text(c) for c in rows[hdr_i]]
    last = -1
    for i, h in enumerate(headers_raw):
        if h:
            last = i
    if last < 0:
        return [], []
    headers = unique_headers(headers_raw[: last + 1])
    data: List[List[Any]] = []
    raw_headers = rows[hdr_i]
    for row in rows[hdr_i + 1 :]:
        if _is_skip_row(row, kind=kind, headers=raw_headers, amount_i=None):
            continue
        clipped = list(row[: len(headers)])
        while len(clipped) < len(headers):
            clipped.append(None)
        clipped = [
            None if (isinstance(c, str) and c.strip().startswith("#")) else c
            for c in clipped
        ]
        data.append(clipped)
        if len(data) >= max_rows:
            break
    return headers, data
