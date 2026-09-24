"""Orchestrate one cre_cleaner run."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from cre_cleaner.adapters import get_adapter
from cre_cleaner.class_labels import (
    claims_class_hint,
    group_rows_by_class,
    ordered_class_labels,
    premium_class_hint,
)
from cre_cleaner.io_excel import write_output_workbook
from cre_cleaner.models import PipelineResult, ExceptionRecord
from cre_cleaner.quarterly import merge_monthly_premiums, parse_claims_file
from cre_cleaner.reconcile import build_summary, flag_duplicate_claims


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


def run_pipeline(
    *,
    cedant: str,
    broker: str,
    year: int,
    quarter: int,
    raw_dir: Path,
    template: Path,
    out_dir: Path,
    base_dir: Optional[Path] = None,
    collapsed: bool = False,
) -> PipelineResult:
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

    adapter = get_adapter(cedant, broker)
    result = PipelineResult()

    # --- Premium ---
    prem_discovered = adapter.discover_premium_files(raw_dir, year, quarter)
    if not prem_discovered:
        result.exceptions.append(ExceptionRecord(
            "ERROR", "no_premium_files",
            detail=f"No monthly premium files for Q{quarter} {year} in {raw_dir}",
        ))
    month_files = []
    for month, path in prem_discovered:
        label = adapter.month_label(month)
        month_files.append((month, path, label))

    premium_rows, exc_p, audit_p = merge_monthly_premiums(month_files)
    result.premium_rows = premium_rows
    result.exceptions.extend(exc_p)
    result.source_audit.extend(audit_p)

    # --- Claims ---
    claims_files = adapter.discover_claims_files(raw_dir, year, quarter)
    paid_all = []
    ost_all = []
    for cpath in claims_files:
        try:
            paid, ost, exc_c, audit_c = parse_claims_file(cpath)
            paid_all.extend(paid)
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
    result.exceptions.extend(flag_duplicate_claims(paid_all, "CLAIMS BORDEREAU"))
    result.exceptions.extend(flag_duplicate_claims(ost_all, "OUTSTANDING LOSS BORDEREAU"))

    by_class = _by_class_counts(
        result.premium_rows, result.claims_rows, result.outstanding_rows
    )
    layout_note = (
        "collapsed single PREMIUM/CLAIMS/OUTSTANDING sheets"
        if collapsed
        else "class-split sheets ({Class} - PREMIUM|CLAIMS|OUTSTANDING) like Bisola"
    )
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S WAT")
    summary = build_summary(
        cedant=cedant.upper(),
        broker=broker.upper(),
        year=year,
        quarter=quarter,
        generated_at=generated_at,
        premium_files=[p.name for _, p in prem_discovered],
        claims_files=[p.name for p in claims_files],
        premium_rows=result.premium_rows,
        claims_rows=result.claims_rows,
        outstanding_rows=result.outstanding_rows,
        exceptions=result.exceptions,
        notes=(
            f"Default layout: {layout_note}. "
            "Paid vs Outstanding kept separate; negatives preserved; "
            "CHANNEL/SUB CHANNEL blank when absent; Surplus block mapped from 1SURP/2SURP/QUOTA. "
            "AIICO ARK subclasses (GIT/All Risks/…) mapped to General Accident."
        ),
    )
    summary["by_class"] = by_class
    summary["output_layout"] = "collapsed" if collapsed else "class-split"
    result.summary = summary

    out_name = f"{cedant.upper()}_{broker.upper()}_{year}_Q{quarter}_cleaned.xlsx"
    out_path = out_dir / out_name
    write_output_workbook(
        template_path=template,
        output_path=out_path,
        premium_rows=result.premium_rows,
        claims_rows=result.claims_rows,
        outstanding_rows=result.outstanding_rows,
        exceptions=result.exceptions,
        source_audit=result.source_audit,
        summary=summary,
        collapsed=collapsed,
    )
    result.output_path = str(out_path)
    return result
