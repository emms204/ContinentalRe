"""Counts + sum checks → SUMMARY / variances.

Also: per-row split checks (shares add to 100%, amounts add to gross/total),
per-row date checks, and source-vs-output reconciliation by month and quarter.
All checks only flag (exceptions / SUMMARY); none of them drops or edits rows.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from src.domain.cre_cleaner.config import MONTH_ALIASES, MONTH_NAMES, QUARTER_MONTHS
from src.domain.cre_cleaner.models import PremiumRow, ClaimsRow, ExceptionRecord, SourceAuditRecord
from src.domain.cre_cleaner.core.normalize import parse_number

_AMOUNT_TOL_ABS = 1.0
# Source-vs-output reconciliation (Manual Table 13, IMPL-20261006-04): the
# output is compared with the sum of the source detail rows; a difference
# below half a cent is a match. Printed totals are a separate comparison.
_RECON_MATCH_TOL = 0.005
# Manual "Reconciliation rules": 0.05 for printed-total rounding. A printed
# total within it is INFO, beyond it WARN; never a reconciliation failure.
_PRINTED_TOTAL_ROUNDING = 0.05
_AMOUNT_TOL_REL = 0.001
_SHARE_TOL_PCT = 0.5  # percentage points

METRIC_LABELS = {
    "gross": "Gross premium",
    "total": "Total claims",
    "ret": "Retention",
    "treaty": "Treaty (TREATY band)",
    "fac": "Facultative",
}
_METRIC_ORDER = ("gross", "total", "ret", "treaty", "fac")


def metric_label(key: str) -> str:
    if key.startswith("layer:"):
        return f"Treaty layer {key[6:]} (not in upload columns)"
    return METRIC_LABELS.get(key, key)


def row_amounts(row) -> Dict[str, float]:
    """Amount metrics carried by one parsed row (used for source row sums and
    for output totals so both sides are measured the same way)."""
    if isinstance(row, PremiumRow):
        d = {"gross": row.gross_premium, "ret": row.ret_prem, "treaty": row.sur_prem, "fac": row.fac_prem}
        layer_key = "prem"
    else:
        d = {"total": row.total_claims, "ret": row.amount_ret, "treaty": row.amount_treaty,
             "fac": row.amount_fac}
        layer_key = "amount"
    out = {k: float(v) for k, v in d.items() if v is not None}
    for layer in getattr(row, "extra_layers", None) or []:
        v = layer.get(layer_key)
        if v is not None:
            k = f"layer:{layer.get('layer')}"
            out[k] = out.get(k, 0.0) + float(v)
    return out


def _amount_tol(base: float) -> float:
    return max(_AMOUNT_TOL_ABS, abs(base) * _AMOUNT_TOL_REL)


def _band_sum_state(total: float, parts: Sequence[Optional[float]], tol: float) -> Tuple[str, float]:
    """ok | mismatch | not_checkable for parts that should add up to total.

    A missing part is unknown (not zero): with unknowns the row is only judged
    when the known parts already match, or already exceed, the total."""
    known = [p for p in parts if p is not None]
    s = sum(known)
    diff = total - s
    if abs(diff) <= tol:
        return "ok", diff
    if len(known) == len(parts) or s > total + tol:
        return "mismatch", diff
    return "not_checkable", diff


def _combine(states: Sequence[str]) -> str:
    if "mismatch" in states:
        return "mismatch"
    if "ok" in states:
        return "ok"
    return "not_checkable"


def check_row_splits(
    premium_rows: Sequence[PremiumRow],
    claims_rows: Sequence[ClaimsRow],
    outstanding_rows: Sequence[ClaimsRow],
) -> Tuple[List[ExceptionRecord], Dict[str, Dict[str, int]]]:
    """Tally RET/TREATY/FAC vs gross (and claim totals) for SUMMARY counts.

    Source/gold often keep rows that do not balance or where treaty equals
    retention; those are left unchanged and are not WARN'd
    (``split_mismatch`` / ``treaty_equals_retention`` suppressed).
    """
    counts: Dict[str, Dict[str, int]] = {}

    c = counts.setdefault("premium", {"ok": 0, "mismatch": 0, "not_checkable": 0})
    for r in premium_rows:
        layers = r.extra_layers or []
        states: List[str] = []
        amounts = [r.ret_prem, r.sur_prem, r.fac_prem] + [l.get("prem") for l in layers]
        if r.gross_premium is not None and any(a is not None for a in amounts):
            st, _diff = _band_sum_state(r.gross_premium, amounts, _amount_tol(r.gross_premium))
            states.append(st)
        shares = [r.ret_ppn, r.sur_ppn, r.fac_ppn] + [l.get("ppn") for l in layers]
        known = [s for s in shares if s is not None]
        if known:
            target = 1.0 if max(abs(s) for s in known) <= 1.0 and sum(known) <= 1.05 else 100.0
            st, _ = _band_sum_state(target, shares, _SHARE_TOL_PCT * target / 100.0)
            states.append(st)
        state = _combine(states) if states else "not_checkable"
        c[state] += 1

    for label, rows in (("claims", claims_rows), ("outstanding", outstanding_rows)):
        c = counts.setdefault(label, {"ok": 0, "mismatch": 0, "not_checkable": 0})
        for r in rows:
            parts = [r.amount_ret, r.amount_treaty, r.amount_fac] + [
                l.get("amount") for l in (r.extra_layers or [])
            ]
            if r.total_claims is None or not any(p is not None for p in parts):
                c["not_checkable"] += 1
                continue
            st, _diff = _band_sum_state(r.total_claims, parts, _amount_tol(r.total_claims))
            c[st] += 1
    return [], counts


def _quarter_bounds(year: int, quarter: int) -> Tuple[datetime, datetime]:
    months = QUARTER_MONTHS[quarter]
    start = datetime(year, months[0], 1)
    end = datetime(year + 1, 1, 1) if months[-1] == 12 else datetime(year, months[-1] + 1, 1)
    return start, end


def _period_column_mapped(row, side: str) -> bool:
    """True when this row's FROM/TO column was mapped (or a PERIOD cell covers it).

    Default True keeps rows built outside the parser on the old blank-cell check.
    """
    return bool(getattr(getattr(row, "audit", None), f"period_{side}_mapped", True))


def check_row_dates(
    premium_rows: Sequence[PremiumRow],
    claims_rows: Sequence[ClaimsRow],
    outstanding_rows: Sequence[ClaimsRow],
    *,
    year: int,
    quarter: int,
) -> Tuple[List[ExceptionRecord], Dict[str, int]]:
    """Flag implausible dates, FROM after TO, and missing date fields.
    Cover start outside the reporting month/quarter is common on gold
    (annual and multi-year policies) and is not flagged.
    Implausible far-future/past dates are collapsed to one WARN summary.
    Unparseable date text is flagged at parse time (``date_unparseable``)."""
    exc: List[ExceptionRecord] = []
    counts: Dict[str, int] = defaultdict(int)
    q_start, q_end = _quarter_bounds(year, quarter)
    implausible_hits: List[str] = []

    def flag(r, sev: str, reason: str, detail: str):
        a = r.audit
        exc.append(ExceptionRecord(sev, reason, a.source_filename, a.source_sheet, a.source_row, detail))
        counts[reason] += 1

    def note_implausible(bordereau: str, name: str, d: datetime):
        if 1950 <= d.year <= year + 5:
            return
        implausible_hits.append(f"{bordereau} {name}={d:%d/%m/%Y}")

    def from_after_to(r, bordereau: str):
        if isinstance(r.period_from, datetime) and isinstance(r.period_to, datetime) \
                and r.period_from > r.period_to:
            flag(r, "WARN", "date_from_after_to",
                 f"{bordereau}: FROM {r.period_from:%d/%m/%Y} after TO {r.period_to:%d/%m/%Y}")

    for r in premium_rows:
        for name, d in (("FROM", r.period_from), ("TO", r.period_to)):
            if isinstance(d, datetime):
                note_implausible("PREMIUM", name, d)
        from_after_to(r, "PREMIUM")
        if r.period_from is None and _period_column_mapped(r, "from"):
            flag(r, "WARN", "period_from_missing",
                 f"PREMIUM: policy={r.policy_no!r} FROM blank")
        if r.period_to is None and _period_column_mapped(r, "to"):
            flag(r, "WARN", "period_to_missing",
                 f"PREMIUM: policy={r.policy_no!r} TO blank")

    for label, rows in (("CLAIMS", claims_rows), ("OUTSTANDING", outstanding_rows)):
        for r in rows:
            for name, d in (("DATE OF LOSS", r.date_of_loss), ("FROM", r.period_from),
                            ("TO", r.period_to), ("PAYMENT DATE", r.paid_date)):
                if isinstance(d, datetime):
                    note_implausible(label, name, d)
            from_after_to(r, label)
            if r.period_from is None and _period_column_mapped(r, "from"):
                flag(r, "WARN", "period_from_missing",
                     f"{label}: claim={r.claim_no!r} FROM blank")
            if r.period_to is None and _period_column_mapped(r, "to"):
                flag(r, "WARN", "period_to_missing",
                     f"{label}: claim={r.claim_no!r} TO blank")
            if r.date_of_loss is None:
                flag(r, "WARN", "date_of_loss_missing", f"{label}: claim={r.claim_no!r}")
            elif isinstance(r.date_of_loss, datetime) and r.date_of_loss >= q_end:
                flag(r, "WARN", "date_of_loss_after_quarter",
                     f"{label}: claim={r.claim_no!r} loss {r.date_of_loss:%d/%m/%Y} after Q{quarter} {year}")
            if label == "CLAIMS" and isinstance(r.paid_date, datetime) and not (q_start <= r.paid_date < q_end):
                flag(r, "WARN", "claim_paid_outside_quarter",
                     f"claim={r.claim_no!r}: paid {r.paid_date:%d/%m/%Y}, outside Q{quarter} {year}")

    if implausible_hits:
        tallies = Counter(implausible_hits)
        samples = ", ".join(f"{k} ×{n}" if n > 1 else k for k, n in tallies.most_common(5))
        more = len(tallies) - min(5, len(tallies))
        detail = (
            f"{len(implausible_hits)} date(s) outside 1950–{year + 5} "
            f"(e.g. {samples}"
            + (f", +{more} more distinct" if more > 0 else "")
            + "); rows kept"
        )
        exc.append(ExceptionRecord("WARN", "date_implausible", detail=detail))
        counts["date_implausible"] = len(implausible_hits)
    return exc, dict(counts)


_BORDEREAU_OF_SHEET_TYPE = {"premium": "PREMIUM", "paid": "CLAIMS", "outstanding": "OUTSTANDING"}


def _period_of(rec: SourceAuditRecord) -> Tuple[str, str]:
    """(level, period) for a source sheet: premium by source month; claims by
    a month named in the sheet (e.g. 'MISC. ACC PAID OCTOBER') else quarter."""
    if rec.sheet_type == "premium":
        m = (rec.source_month or "").upper()
        if m in MONTH_ALIASES:
            return "Month", m
        return "Quarter file", rec.source_month or "(quarter)"
    toks = [t for t in rec.source_sheet.upper().replace(".", " ").split() if t]
    months = {MONTH_ALIASES[t] for t in toks if t in MONTH_ALIASES}
    if len(months) == 1:
        return "Month", MONTH_NAMES[months.pop()]
    return "Quarter file", "(quarter)"


def build_source_reconciliation(
    source_audit: Sequence[SourceAuditRecord],
    premium_rows: Sequence[PremiumRow],
    claims_rows: Sequence[ClaimsRow],
    outstanding_rows: Sequence[ClaimsRow],
    *,
    currency: str,
    year: int,
    quarter: int,
) -> List[dict]:
    """Rows, gross/total, retention, each treaty layer and FAC: output vs the
    cedant's own total rows (when present) and vs the sum of parsed source
    rows — month by month, then the quarter. One currency at a time."""
    recs = [r for r in source_audit if r.sheet_type in _BORDEREAU_OF_SHEET_TYPE]
    rec_by_sheet = {(r.source_filename, r.source_sheet): r for r in recs}

    out_rows: Dict[Tuple[str, str], list] = defaultdict(list)
    for rows in (premium_rows, claims_rows, outstanding_rows):
        for r in rows:
            out_rows[(r.audit.source_filename, r.audit.source_sheet)].append(r)

    table: List[dict] = []
    for sheet_type, bordereau in _BORDEREAU_OF_SHEET_TYPE.items():
        groups: Dict[Tuple[str, str], List[SourceAuditRecord]] = defaultdict(list)
        for rec in recs:
            # Only audits that actually kept rows for this currency. Empty
            # "not loaded here" stubs (premium sheets seen while parsing claims,
            # and the reverse) share the sheet key and used to create a second
            # period group whose Output doubled the Quarter total.
            if rec.sheet_type == sheet_type and rec.parsed_rows.get(currency):
                groups[_period_of(rec)].append(rec)
        if not groups:
            continue
        quarter_acc: Dict[str, dict] = {}
        for (level, period), group in sorted(groups.items(), key=lambda kv: _period_sort(kv[0])):
            lines = _recon_lines(group, out_rows, rec_by_sheet, currency)
            for key, line in lines.items():
                acc = quarter_acc.setdefault(key, {"src": 0.0, "out": 0.0, "footer": 0.0,
                                                   "footer_ok": True, "any": False})
                acc["src"] += line["source_row_sum"] or 0.0
                acc["out"] += line["output"] or 0.0
                acc["any"] = True
                if line["source_total_row"] is None:
                    acc["footer_ok"] = False
                else:
                    acc["footer"] += line["source_total_row"]
                table.append(_recon_record(level, period, bordereau, key, line))
        if len(groups) > 1:
            for key, acc in quarter_acc.items():
                line = {
                    "source_row_sum": acc["src"],
                    "output": acc["out"],
                    "source_total_row": acc["footer"] if acc["footer_ok"] and key not in ("rows", "hidden") else None,
                }
                table.append(_recon_record("Quarter", f"Q{quarter} {year}", bordereau, key, line))
    return table


def _period_sort(key: Tuple[str, str]):
    level, period = key
    return (level != "Month", MONTH_ALIASES.get(period, 99), period)


def _recon_lines(group, out_rows, rec_by_sheet, currency) -> Dict[str, dict]:
    src_rows = sum(r.parsed_rows.get(currency, 0) for r in group)
    hidden = sum(r.hidden_rows for r in group if r.currency in (currency, "MIXED"))
    # One output list per source sheet. A claims file run with bordereau_type=all
    # also goes through the premium reader, which adds empty "not loaded here"
    # paid/outstanding audit stubs for the same sheet — counting those twice
    # doubles Output against a correct Source row sum.
    output_rows = []
    seen_sheets: set = set()
    for rec in group:
        sheet_key = (rec.source_filename, rec.source_sheet)
        if sheet_key in seen_sheets:
            continue
        seen_sheets.add(sheet_key)
        output_rows.extend(
            r for r in out_rows.get(sheet_key, [])
            if r.audit.currency == currency
        )
    lines: Dict[str, dict] = {
        "rows": {"source_row_sum": float(src_rows), "output": float(len(output_rows)),
                 "source_total_row": None},
    }
    if hidden:
        lines["hidden"] = {"source_row_sum": float(hidden), "output": 0.0, "source_total_row": None}
    src_tot: Dict[str, float] = defaultdict(float)
    for rec in group:
        for k, v in (rec.parsed_totals.get(currency) or {}).items():
            src_tot[k] += v
    out_tot: Dict[str, float] = defaultdict(float)
    for r in output_rows:
        for k, v in row_amounts(r).items():
            out_tot[k] += v
    keys = [k for k in _METRIC_ORDER if k in src_tot or k in out_tot]
    keys += sorted(k for k in set(src_tot) | set(out_tot) if k.startswith("layer:"))
    single_ccy = all(rec.currency == currency for rec in group if rec.parsed_rows.get(currency))
    for k in keys:
        footer = None
        with_rows = [rec for rec in group if rec.parsed_rows.get(currency)]
        if single_ccy and with_rows and all(k in rec.footer_totals for rec in with_rows):
            footer = sum(rec.footer_totals[k] for rec in with_rows)
        lines[k] = {"source_row_sum": src_tot.get(k, 0.0), "output": out_tot.get(k, 0.0),
                    "source_total_row": footer}
    return lines


def _recon_record(level: str, period: str, bordereau: str, key: str, line: dict) -> dict:
    """One SUMMARY reconciliation line. Amounts: output vs the sum of the
    source detail rows (Manual Table 13); |difference| < 0.005 is a match. A
    printed source total is shown beside it as its own comparison
    (``printed_total_variance`` = printed total − detail rows) and never turns
    a matching line into a variance."""
    src, out, footer = line["source_row_sum"], line["output"], line["source_total_row"]
    if key == "rows":
        var = out - src
        status = "Match" if var == 0 else "Variance — see exceptions"
        return {"level": level, "period": period, "bordereau": bordereau, "metric": "Rows",
                "source_total_row": None, "source_row_sum": int(src), "output": int(out),
                "variance": int(var), "status": status, "printed_total_variance": None}
    if key == "hidden":
        return {"level": level, "period": period, "bordereau": bordereau,
                "metric": "Hidden rows skipped", "source_total_row": None,
                "source_row_sum": int(src), "output": 0, "variance": -int(src),
                "status": "Excluded (hidden in source)", "printed_total_variance": None}
    diff = out - src
    ok = abs(diff) < _RECON_MATCH_TOL
    var = 0.0 if ok else round(diff, 2)
    status = "Match" if ok else "Variance"
    printed = None
    if footer is not None:
        pdiff = footer - src
        printed = 0.0 if abs(pdiff) < _RECON_MATCH_TOL else round(pdiff, 2)
        if printed:
            status += " (printed total differs — see printed_total_variance)"
    else:
        status += " (no printed source total)"
    return {"level": level, "period": period, "bordereau": bordereau, "metric": metric_label(key),
            "source_total_row": round(footer, 2) if footer is not None else None,
            "source_row_sum": round(src, 2), "output": round(out, 2), "variance": var,
            "status": status, "printed_total_variance": printed}


def _footer_cell(where: str) -> str:
    """First printed-total cell, plus a count when several subtotal cells were added."""
    parts = [p.strip() for p in (where or "").split(",") if p.strip()]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} (+{len(parts) - 1} cells)"


def printed_total_variances(source_audit: Sequence[SourceAuditRecord]) -> List[ExceptionRecord]:
    """Manual Table 13 / 14: every printed source total that differs from the
    legible detail rows is documented in one summary. The summary gives the
    net gap by bordereau and currency, each mismatch (file, sheet, cell,
    printed vs detail), and which other printed totals on that sheet still
    match. Detail rows are never altered and the reconciliation does not
    fail. WARN when any gap exceeds the 0.05 printed-total rounding
    tolerance, otherwise INFO. Sheets with several currencies are omitted
    (their printed total cannot be attributed to one currency)."""
    hits = []
    compared = 0
    skipped_mixed = 0
    matched: Dict[tuple, List[str]] = defaultdict(list)
    for rec in source_audit:
        if rec.sheet_type not in _BORDEREAU_OF_SHEET_TYPE or not rec.footer_totals:
            continue
        totals = {c: t for c, t in (rec.parsed_totals or {}).items() if t}
        if len(totals) != 1 or rec.currency == "MIXED":
            skipped_mixed += 1
            continue
        ccy, src_tot = next(iter(totals.items()))
        bordereau = _BORDEREAU_OF_SHEET_TYPE[rec.sheet_type]
        sheet_key = (rec.source_filename or "", rec.source_sheet, bordereau)
        cells = getattr(rec, "footer_cells", None) or {}
        keys = [k for k in _METRIC_ORDER if k in rec.footer_totals]
        keys += sorted(k for k in rec.footer_totals if k.startswith("layer:"))
        for k in keys:
            compared += 1
            printed = float(rec.footer_totals[k])
            detail_sum = float(src_tot.get(k, 0.0))
            diff = printed - detail_sum
            label = metric_label(k)
            if abs(diff) < _RECON_MATCH_TOL:
                matched[sheet_key].append(label)
                continue
            hits.append({
                "bordereau": bordereau,
                "metric": label,
                "file": rec.source_filename or "",
                "sheet": rec.source_sheet,
                "where": cells.get(k, ""),
                "ccy": ccy,
                "printed": printed,
                "detail": detail_sum,
                "diff": diff,
                "sheet_key": sheet_key,
            })
    if not hits:
        return []
    hits.sort(key=lambda h: abs(h["diff"]), reverse=True)
    material = sum(1 for h in hits if abs(h["diff"]) > _PRINTED_TOTAL_ROUNDING)
    rounding = len(hits) - material
    if material and rounding:
        scale = f"{material} above 0.05 rounding, {rounding} within rounding"
    elif material:
        scale = "all above 0.05 rounding"
    else:
        scale = "all within 0.05 printed-total rounding"

    book_order = ("PREMIUM", "CLAIMS", "OUTSTANDING")
    nets: Dict[tuple, float] = defaultdict(float)
    net_n: Dict[tuple, int] = defaultdict(int)
    for h in hits:
        key = (h["bordereau"], h["ccy"])
        nets[key] += h["diff"]
        net_n[key] += 1

    def _signed(amount: float) -> str:
        sign = "+" if amount > 0 else "−"
        return f"{sign}{abs(amount):,.2f}"

    net_bits = []
    for key in sorted(nets, key=lambda bc: (book_order.index(bc[0]) if bc[0] in book_order else 9, bc[1])):
        bord, ccy = key
        n = net_n[key]
        net_bits.append(f"{bord} {ccy} {_signed(nets[key])} across {n} metric" + ("s" if n != 1 else ""))

    noted: set = set()

    def _one(h: dict) -> str:
        loc = f"{h['sheet']}!{_footer_cell(h['where'])}"
        if h["file"]:
            loc = f"{h['file']} / {loc}"
        direction = "higher" if h["diff"] > 0 else "lower"
        text = (
            f"{h['bordereau']} {h['metric']} — {loc}: printed {h['printed']:,.2f} vs "
            f"detail rows {h['detail']:,.2f} {h['ccy']} "
            f"(printed is {abs(h['diff']):,.2f} {direction})"
        )
        if h["sheet_key"] not in noted:
            noted.add(h["sheet_key"])
            mates = matched.get(h["sheet_key"]) or []
            if mates:
                text += f"; matching printed totals on this sheet: {', '.join(mates)}"
        return text

    shown = hits[:12]
    body = "; ".join(_one(h) for h in shown)
    extra = ""
    if len(hits) > len(shown):
        rest = hits[len(shown):]
        extra = (
            f"; +{len(rest)} more "
            f"(combined |printed − detail| {sum(abs(h['diff']) for h in rest):,.2f})"
        )
    mixed = ""
    if skipped_mixed:
        mixed = (
            f" {skipped_mixed} mixed-currency sheet(s) omitted "
            "(a printed total there cannot be tied to one currency)."
        )
    detail = (
        f"{len(hits)} of {compared} printed footer total(s) differ from the sum of "
        f"the detail rows that were kept ({scale}). "
        f"Net printed − detail: {'; '.join(net_bits)}. "
        "Those detail rows were kept and are what reconciliation uses "
        f"(Manual Table 13). {body}{extra}.{mixed}"
    )
    sev = "WARN" if material else "INFO"
    return [ExceptionRecord(sev, "printed_total_variance", detail=detail)]


def _sum_attr(rows: Sequence, attr: str):
    total = 0.0
    n = 0
    for r in rows:
        v = getattr(r, attr, None)
        if v is None:
            continue
        n += 1
        total += float(v)
    return total if n else 0.0


def build_summary(
    *,
    cedant: str,
    broker: str,
    year: int,
    quarter: int,
    generated_at: str,
    premium_files: List[str],
    claims_files: List[str],
    premium_rows: Sequence[PremiumRow],
    claims_rows: Sequence[ClaimsRow],
    outstanding_rows: Sequence[ClaimsRow],
    exceptions: Sequence[ExceptionRecord],
    notes: str = "",
) -> dict:
    ost_supplied = "Yes" if outstanding_rows else "Not Supplied"
    if not outstanding_rows:
        notes = (notes + "; " if notes else "") + "OUTSTANDING LOSS BORDEREAU empty (Not Supplied or no parseable OST rows)."

    # Flag apparent duplicate claim keys (do not delete). Same claim with a
    # different cover period is not a duplicate.
    seen = {}
    for r in list(claims_rows) + list(outstanding_rows):
        key = (r.claim_no or "", r.policy_no or "", str(r.date_of_loss),
               str(r.period_from), str(r.period_to), r.total_claims)
        if not r.claim_no:
            continue
        if key in seen:
            pass
        seen[key] = seen.get(key, 0) + 1
    dup_keys = sum(1 for k, v in seen.items() if v > 1)

    return {
        "cedant": cedant,
        "broker": broker,
        "year": year,
        "quarter": quarter,
        "generated_at": generated_at,
        "premium_files": ", ".join(premium_files) if premium_files else "(none)",
        "claims_files": ", ".join(claims_files) if claims_files else "(none)",
        "premium_rows": len(premium_rows),
        "claims_rows": len(claims_rows),
        "outstanding_rows": len(outstanding_rows),
        "exception_rows": len(exceptions),
        "premium_gross_sum": _sum_attr(premium_rows, "gross_premium"),
        "claims_total_sum": _sum_attr(claims_rows, "total_claims"),
        "outstanding_total_sum": _sum_attr(outstanding_rows, "total_claims"),
        "outstanding_supplied": ost_supplied,
        "apparent_duplicate_claim_keys": dup_keys,
        "notes": notes,
    }


def _key_num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return v if v not in ("", None) else None


def transaction_key(row) -> tuple:
    """Identity of one transaction row across files (secondary-source check)."""
    if isinstance(row, ClaimsRow):
        return ("C", str(row.claim_no or "").strip().upper(), str(row.policy_no or "").strip().upper(),
                str(row.date_of_loss), _key_num(row.total_claims), _key_num(row.amount_ret),
                _key_num(row.amount_treaty))
    return ("P", str(row.policy_no or "").strip().upper(), str(row.name_of_insured or "").strip().upper(),
            _key_num(row.gross_premium), _key_num(row.ret_prem), _key_num(row.sur_prem),
            str(row.period_from), str(row.period_to))


def overlap_counts(candidate_rows: Sequence, loaded_rows: Sequence) -> Tuple[int, int]:
    """(rows already loaded, rows not loaded) — multiset match on transaction_key."""
    from collections import Counter
    pool = Counter(transaction_key(r) for r in loaded_rows)
    dup = 0
    for r in candidate_rows:
        k = transaction_key(r)
        if pool[k] > 0:
            pool[k] -= 1
            dup += 1
    return dup, len(candidate_rows) - dup


def flag_duplicate_claims(rows: Sequence[ClaimsRow], source_label: str) -> List[ExceptionRecord]:
    """No-op: repeated claim rows are kept as in the source and not flagged.

    Exact repeats are expected when the cedant lists the same transaction more
    than once (or the same line appears across monthly inputs). Deleting or
    warning would invent a different bordereau than Cont Re received.
    """
    return []


def flag_duplicate_premium(rows: Sequence, source_label: str = "PREMIUM BORDEREAU") -> List[ExceptionRecord]:
    """No-op: repeated premium rows are kept as in the source and not flagged.

    Same policy/period/amounts appearing twice is left for Cont Re to handle
    upstream; the cleaner must not warn on expected passthrough behaviour.
    """
    return []
