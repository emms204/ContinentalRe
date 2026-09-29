"""Sheet-derived totals for compare (do not trust SUMMARY sheets)."""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cre_cleaner.core.filters import looks_like_total_row
from cre_cleaner.io.excel import read_workbook_sheets as _read_workbook_sheets
from cre_cleaner.io.excel import sheet_names as _sheet_names
from cre_cleaner.core.normalize import clean_text, normalize_header, parse_number


SKIP_SHEETS = {"SUMMARY", "SOURCE AUDIT", "EXCEPTIONS"}


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


def _is_fac_sheet(name: str) -> bool:
    n = clean_text(name).upper()
    return "FACULTATIVE" in n or n.strip().startswith("FAC ")


def _classify_sheet(name: str) -> Optional[str]:
    """Return premium | paid | outstanding, or None to skip."""
    n = clean_text(name).upper()
    if not n or n in SKIP_SHEETS:
        return None
    # Bisola quirks: "2ND SURPLUS … PREM", "… OS", leading-space OUTSTANDING,
    # and titles truncated at Excel's 31-char limit ("2ND SURPLUS Engineering -
    # Outst", "2ND SURPLUS Marine Cargo - Clai") — match on word stems.
    is_ost = (
        "OUTS" in n            # OUTSTANDING / OUTST / OUTS (truncated)
        or "OUTSAND" in n      # source typo
        or n.rstrip().endswith(" OS")
        or " - OS" in n
    )
    is_claim = "CLAI" in n     # CLAIM / CLAIMS / CLAI (truncated)
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
    # Bisola footers: blank policy + blank insured, but many numeric totals
    insured = clean_text(row[insured_i]) if insured_i is not None and insured_i < len(row) else ""
    policy = clean_text(row[policy_i]) if policy_i is not None and policy_i < len(row) else ""
    if not insured and not policy:
        return True
    non_empty = sum(1 for c in row if c is not None and clean_text(c))
    if non_empty < 2:
        return True
    if amount_i is not None and amount_i < len(row):
        # lone amount with no identity already handled above
        pass
    return False


def _sheet_row_amount(
    rows: List[List[Any]], kind: str
) -> Tuple[int, float]:
    hdr_i = _find_header_row(rows, kind)
    if hdr_i is None:
        return 0, 0.0
    headers = rows[hdr_i]
    amount_i = _amount_col_index(headers, kind)
    n = 0
    total = 0.0
    for row in rows[hdr_i + 1 :]:
        if _is_skip_row(row, kind=kind, headers=headers, amount_i=amount_i):
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
) -> WorkbookMetrics:
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
        data_sheets.append(sn)
        rows = sheets.get(sn) or []
        count, amount = _sheet_row_amount(rows, kind)
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
    )


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
        # fallback: first non-empty row
        for i, row in enumerate(rows[:8]):
            if any(c is not None and clean_text(c) for c in row):
                hdr_i = i
                break
    if hdr_i is None:
        return [], []
    headers_raw = [clean_text(c) for c in rows[hdr_i]]
    # Keep only through the last real header — skip empty TEMPLATE padding cols
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
        # Surface Excel errors clearly without inventing numbers
        clipped = [
            None if (isinstance(c, str) and c.strip().startswith("#")) else c
            for c in clipped
        ]
        data.append(clipped)
        if len(data) >= max_rows:
            break
    return headers, data
