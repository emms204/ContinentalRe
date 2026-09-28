"""Mimic the Continental Re upload validator on a cleaned workbook.

Usage: python tests/validate_upload.py OUTPUT.xlsx [GOLD.xlsx]
Per sheet (SUMMARY reported separately):
 a) duplicate headers (exact + normalized)      b) blank headers inside the header span
 c) non-empty cells right of last header col     d) hidden columns/rows/sheets
 e) whitespace-only cells                        f) Excel error strings / date-overflow reads
 g) sheet names vs gold                          h) header|first-row reconstruction duplicates
Plus column-by-column header comparison vs gold per sheet type.

Layout: a leading column that is *fully empty* (no value in any row, incl. the
title row) before the first header is the expected CLAIMS/OUTSTANDING layout
(TEMPLATE.xlsx / Bisola gold: column A empty, headers B..S) and is not flagged.
A leading blank header column that holds any value IS flagged under b), as is
any blank header between the first and last header.
"""
from __future__ import annotations

import re
import sys
import warnings
from collections import Counter
from datetime import date, datetime

from openpyxl import load_workbook

ERRORS = {"#VALUE!", "#REF!", "#DIV/0!", "#N/A", "#NAME?", "#NUM!", "#NULL!"}


def norm(h) -> str:
    s = "" if h is None else str(h)
    s = re.sub(r"\s+", " ", s).strip().upper()
    return s.rstrip(" .:")


def sheet_type(title: str) -> str:
    t = title.upper()
    if "PREM" in t:
        return "premium"
    if "OUTST" in t or t.endswith(" OS"):
        return "outstanding"
    if "CLAIM" in t:
        return "claims"
    return "other"


def header_row_of(ws) -> int | None:
    for r in range(1, 10):
        vals = [norm(c.value) for c in ws[r]]
        if any("POLICY NO" in v for v in vals) and any("INSURED" in v for v in vals):
            return r
    return None


def fmt_val(v) -> str:
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def load(path):
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        wb = load_workbook(path)
    overflow = [str(x.message) for x in w if "outside the limits for dates" in str(x.message)]
    return wb, overflow


def check_sheet(ws):
    res = {}
    hr = header_row_of(ws)
    res["header_row"] = hr
    if hr is None:
        res["fatal"] = "no header row"
        return res, None
    raw = [ws.cell(hr, c).value for c in range(1, ws.max_column + 1)]
    filled = [i + 1 for i, v in enumerate(raw) if v is not None and str(v).strip()]
    last = max(filled + [0])
    first = min(filled) if filled else 1
    # Columns before the first header: OK only when fully empty in every row.
    col_has_value = {c: False for c in range(1, first)}
    for row in ws.iter_rows(max_col=max(first - 1, 1)):
        for c in row:
            if c.column < first and c.value is not None:
                col_has_value[c.column] = True
    leading_empty = [c for c in range(1, first) if not col_has_value[c]]
    leading_used = [c for c in range(1, first) if col_has_value[c]]
    res["leading_blank_cols"] = len(leading_empty)
    res["first_header_col"] = first
    headers = raw[first - 1:last]
    res["n_header_cols"] = len(headers)
    res["last_header_col"] = last
    res["max_column"] = ws.max_column
    res["headers"] = headers
    ex = Counter(str(h) for h in headers if h is not None and str(h).strip())
    res["a_dup_exact"] = sorted(k for k, n in ex.items() if n > 1)
    nm = Counter(norm(h) for h in headers if norm(h))
    res["a_dup_normalized"] = sorted(k for k, n in nm.items() if n > 1)
    res["b_blank_headers"] = (
        [ws.cell(hr, c).coordinate for c in leading_used]
        + [ws.cell(hr, c).coordinate for c in range(first, max(last, ws.max_column) + 1)
           if ws.cell(hr, c).value is None or not str(ws.cell(hr, c).value).strip()]
    )
    right, wsonly, errs = [], [], []
    for row in ws.iter_rows():
        for c in row:
            v = c.value
            if v is None:
                continue
            if c.column > last:
                right.append(c.coordinate)
            if isinstance(v, str) and not v.strip():
                wsonly.append(c.coordinate)
            if (isinstance(v, str) and v.strip().upper() in ERRORS) or c.data_type == "e":
                errs.append(f"{c.coordinate}={v!r}")
    res["c_right_of_last_header"] = right
    res["d_hidden_cols"] = [k for k, d in ws.column_dimensions.items() if d.hidden]
    res["d_hidden_rows"] = [k for k, d in ws.row_dimensions.items() if d.hidden]
    res["d_sheet_state"] = ws.sheet_state
    res["e_whitespace_only"] = wsonly
    res["f_error_strings"] = errs
    # h) header | first data row value
    first_row = [ws.cell(hr + 1, c).value for c in range(first, last + 1)]
    recon = []
    for h, v in zip(headers, first_row):
        hs = "" if h is None else str(h)
        recon.append(f"{hs} | {fmt_val(v)}" if v is not None and str(v).strip() else hs)
    rc = Counter(recon)
    res["h_recon_dups"] = sorted(k for k, n in rc.items() if n > 1)
    res["h_recon_tail"] = recon[-6:]
    res["data_rows"] = sum(1 for r in range(hr + 1, ws.max_row + 1)
                           if any(ws.cell(r, c).value not in (None, "") for c in range(1, last + 1)))
    return res, headers


def summary_check(ws):
    ws_only, errs, hidden = [], [], [k for k, d in ws.column_dimensions.items() if d.hidden]
    for row in ws.iter_rows():
        for c in row:
            v = c.value
            if isinstance(v, str) and not v.strip():
                ws_only.append(c.coordinate)
            if (isinstance(v, str) and v.strip().upper() in ERRORS) or c.data_type == "e":
                errs.append(c.coordinate)
    return dict(dims=ws.dimensions, whitespace_only=ws_only, errors=errs, hidden_cols=hidden)


def main(path, gold=None):
    wb, overflow = load(path)
    print(f"# {path}")
    print(f"f) date-overflow reads (cells a reader would show as #VALUE!): {len(overflow)}")
    gold_hdr, gold_by_title, gold_names = {}, {}, []
    if gold:
        gwb, _ = load(gold)
        gold_names = gwb.sheetnames
        for gs in gwb.worksheets:
            t = sheet_type(gs.title)
            if t in ("premium", "claims", "outstanding") and t not in gold_hdr and "Bond" not in gs.title:
                hr = header_row_of(gs)
                if hr:
                    vals = [gs.cell(hr, c).value for c in range(1, gs.max_column + 1)]
                    while vals and (vals[-1] is None or not str(vals[-1]).strip()):
                        vals.pop()
                    while vals and (vals[0] is None or not str(vals[0]).strip()):
                        vals.pop(0)  # gold claims have a blank col A
                    gold_hdr[t] = (gs.title, vals)
            hr = header_row_of(gs) if t in ("premium", "claims", "outstanding") else None
            if hr:
                vals = [gs.cell(hr, c).value for c in range(1, gs.max_column + 1)]
                while vals and (vals[-1] is None or not str(vals[-1]).strip()):
                    vals.pop()
                while vals and (vals[0] is None or not str(vals[0]).strip()):
                    vals.pop(0)
                cls = gs.title.split(" - ")[0].replace("2ND SURPLUS", "").strip().upper()
                gold_by_title[(cls, t)] = (gs.title, vals)
    total_fail = 0
    for ws in wb.worksheets:
        if ws.title == "SUMMARY":
            print(f"\n[SUMMARY] {summary_check(ws)}")
            continue
        res, headers = check_sheet(ws)
        t = sheet_type(ws.title)
        fails = [k for k in ("a_dup_exact", "a_dup_normalized", "b_blank_headers", "c_right_of_last_header",
                             "d_hidden_cols", "d_hidden_rows", "e_whitespace_only", "f_error_strings",
                             "h_recon_dups") if res.get(k)]
        if res.get("d_sheet_state") not in (None, "visible"):
            fails.append("d_sheet_state")
        total_fail += len(fails)
        status = "PASS" if not fails else "FAIL " + ",".join(fails)
        span = ""
        if res.get("first_header_col"):
            from openpyxl.utils import get_column_letter as _gcl
            span = (f" span={_gcl(res['first_header_col'])}..{_gcl(res['last_header_col'])}"
                    f" leading_empty_cols={res.get('leading_blank_cols')}")
        print(f"\n[{ws.title}] type={t} hdr_row={res.get('header_row')} cols={res.get('n_header_cols')} "
              f"max_col={res.get('max_column')}{span} rows={res.get('data_rows')} -> {status}")
        for k in fails:
            print(f"   {k}: {res[k][:10] if isinstance(res[k], list) else res[k]}")
        if ws.title.startswith("Marine Hull - PREMIUM"):
            print(f"   h) reconstruction tail: {res['h_recon_tail']}")
        cls = ws.title.split(" - ")[0].strip().upper()
        if (t in gold_hdr or (cls, t) in gold_by_title) and headers is not None:
            gtitle, gh = gold_by_title.get((cls, t)) or gold_hdr[t]
            diffs = [(i + 1, g, o) for i, (g, o) in enumerate(zip(gh, headers)) if norm(g) != norm(o)]
            if len(gh) != len(headers):
                diffs.append(("len", len(gh), len(headers)))
            print(f"   vs gold '{gtitle}': {'identical' if not diffs else diffs}")
    if gold:
        ours = set(wb.sheetnames) - {"SUMMARY"}
        gs = set(gold_names) - {"SUMMARY"}
        print(f"\ng) sheet names: ours-only={sorted(ours - gs)} gold-only={sorted(gs - ours)}")
    print(f"\nTOTAL per-sheet check failures: {total_fail}")
    return total_fail


if __name__ == "__main__":
    sys.exit(1 if main(*sys.argv[1:3]) else 0)
