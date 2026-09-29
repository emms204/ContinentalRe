"""Orchestrate one cre_cleaner run."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from cre_cleaner.adapters import get_adapter
from cre_cleaner.core.class_labels import (
    claims_class_hint,
    group_rows_by_class,
    is_fac_class,
    is_unresolved_class,
    ordered_class_labels,
    premium_class_hint,
)
from cre_cleaner.config import QUARTER_MONTHS
from cre_cleaner.io.excel import write_output_workbook
from cre_cleaner.models import PipelineResult, ExceptionRecord
from cre_cleaner.io.pdf import convert_pdfs_in_dir, list_pdfs
from cre_cleaner.core.period_infer import infer_period
from cre_cleaner.core.quarterly import merge_monthly_premiums, parse_claims_file
from cre_cleaner.core.reconcile import (
    build_summary,
    build_source_reconciliation,
    check_row_dates,
    check_row_splits,
    flag_duplicate_claims,
    flag_duplicate_premium,
)


def _by_class_counts(premium_rows, claims_rows, outstanding_rows) -> dict:
    prem_by = group_rows_by_class(premium_rows, premium_class_hint)
    paid_by = group_rows_by_class(claims_rows, claims_class_hint)
    ost_by = group_rows_by_class(outstanding_rows, claims_class_hint)
    labels = ordered_class_labels(set(prem_by) | set(paid_by) | set(ost_by))
    out = {}
    for lab in labels:
        out[lab] = {
            "premium": len(prem_by.get(lab) or []),
            "claims": len(paid_by.get(lab) or []),
            "outstanding": len(ost_by.get(lab) or []),
        }
    return out


def _drop_fac_rows(premium_rows, claims_rows, outstanding_rows):
    """Exclude Facultative-class rows from upload output (Continental guidance)."""
    prem = [r for r in premium_rows if not is_fac_class(premium_class_hint(r))]
    paid = [r for r in claims_rows if not is_fac_class(claims_class_hint(r))]
    ost = [r for r in outstanding_rows if not is_fac_class(claims_class_hint(r))]
    return prem, paid, ost


def _divert_unresolved_class(rows, class_getter, bordereau: str):
    """Rows with no class, or a class too broad to place (bare MARINE), are
    NOT written to an 'Other' sheet; they go to the exceptions sidecar."""
    kept, excs = [], []
    for r in rows:
        hint = (class_getter(r) or "").strip()
        if hint and not is_unresolved_class(hint):
            kept.append(r)
            continue
        a = getattr(r, "audit", None)
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


_BORDEREAU_TYPES = {"all", "premium", "claims", "outstanding"}


def normalize_bordereau_type(value: Optional[str]) -> str:
    """Phase 1 mode: premium | claims | outstanding | all."""
    v = (value or "all").strip().lower()
    if v in {"paid", "claim"}:
        return "claims"
    if v in {"ost", "os", "out"}:
        return "outstanding"
    if v in {"prem", "premiums"}:
        return "premium"
    if v not in _BORDEREAU_TYPES:
        raise ValueError(
            f"bordereau_type must be one of {sorted(_BORDEREAU_TYPES)}, got {value!r}"
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


def run_pipeline(
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
) -> PipelineResult:
    """Clean one quarter from ``raw_dir``.

    Phase 1 defaults: PDF conversion off; year/quarter inferred from sheet
    date columns when omitted; ``bordereau_type`` selects Premium / Claims /
    Outstanding / all.
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
    result = PipelineResult()
    result.exceptions.append(ExceptionRecord(
        "INFO", "adapter_status",
        detail=f"{cedant.upper()}/{broker.upper()}: {adapter.status_text()}",
    ))
    result.exceptions.append(ExceptionRecord(
        "INFO", "bordereau_type",
        detail=f"Phase 1 mode={mode}",
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
    if year is None or quarter is None:
        inferred = infer_period(raw_dir)
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
            result.exceptions.append(ExceptionRecord(
                "ERROR", "period_unresolved",
                detail=(
                    "Could not infer year and quarter from date columns in the "
                    "workbooks (or filenames as fallback). Ensure sheets have "
                    "Date of Loss / Cover From / Transaction Date values."
                ),
            ))
            result.summary = {
                "cedant": cedant, "broker": broker,
                "year": year, "quarter": quarter,
                "adapter_status": adapter.status_text(),
                "bordereau_type": mode,
                "error": "period_unresolved",
            }
            return result

    year = int(year)
    quarter = int(quarter)
    if quarter not in (1, 2, 3, 4):
        result.exceptions.append(ExceptionRecord(
            "ERROR", "period_invalid", detail=f"quarter must be 1–4, got {quarter}",
        ))
        return result

    # --- Premium ---
    month_files: list = []
    prem_mode = "none"
    prem_discovered: list = []
    if want_premium:
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
        claims_files = adapter.discover_claims_files(raw_dir, year, quarter)
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

    if not include_fac:
        result.premium_rows, result.claims_rows, result.outstanding_rows = _drop_fac_rows(
            result.premium_rows, result.claims_rows, result.outstanding_rows
        )

    for attr, getter, label in (
        ("premium_rows", premium_class_hint, "PREMIUM"),
        ("claims_rows", claims_class_hint, "CLAIMS"),
        ("outstanding_rows", claims_class_hint, "OUTSTANDING"),
    ):
        kept, excs = _divert_unresolved_class(getattr(result, attr), getter, label)
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
        by_class = _by_class_counts(prem_c, paid_c, ost_c)
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
