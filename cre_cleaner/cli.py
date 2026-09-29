"""CLI for cre_cleaner."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cre_cleaner.core.detect import detect_sheet_type, find_header_row
from cre_cleaner.io.excel import read_workbook_sheets, sheet_names
from cre_cleaner.core.normalize import normalize_header
from cre_cleaner.pipeline import run_pipeline


def cmd_inspect(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1
    print(f"File: {path}")
    print(f"Sheets: {sheet_names(path)}")
    sheets = read_workbook_sheets(path)
    for sn, rows in sheets.items():
        st = detect_sheet_type(sn, rows[:20])
        hdr = find_header_row(rows, "premium" if st == "premium" else ("outstanding" if st == "outstanding" else "paid"))
        print(f"\n=== {sn!r} type={st} header_row={None if hdr is None else hdr + 1} ===")
        if hdr is not None:
            headers = rows[hdr]
            cols = []
            for i, h in enumerate(headers):
                if h is not None and str(h).strip():
                    cols.append(f"{i + 1}:{normalize_header(h)}")
            print("  columns:", ", ".join(cols))
            if hdr > 0:
                group = [f"{i + 1}:{normalize_header(h)}" for i, h in enumerate(rows[hdr - 1]) if h is not None and str(h).strip()]
                if group:
                    print("  group_row:", ", ".join(group))
        else:
            shown = 0
            for i, row in enumerate(rows[:12]):
                vals = [str(c)[:40] for c in row if c is not None and str(c).strip()]
                if vals:
                    print(f"  R{i + 1}: {vals[:12]}")
                    shown += 1
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    base = Path(args.base_dir).resolve() if args.base_dir else Path.cwd()
    result = run_pipeline(
        cedant=args.cedant,
        broker=args.broker,
        year=args.year,
        quarter=args.quarter,
        raw_dir=Path(args.raw_dir),
        template=Path(args.template),
        out_dir=Path(args.out_dir),
        base_dir=base,
        collapsed=bool(args.collapsed),
        include_audit_sheets=bool(args.include_audit_sheets),
        include_fac=bool(args.include_fac),
        proportion_headers=args.proportion_headers,
        out_name=args.out_name,
        claims_leading_blank=bool(args.claims_leading_blank),
    )
    print("Output:", result.output_path)
    if len(result.outputs) > 1:
        print("Currency workbooks:")
        for e in result.outputs:
            print(f"  {e['currency']}: {e['output_path']} "
                  f"(prem={e['premium_rows']} paid={e['claims_rows']} ost={e['outstanding_rows']})")
    print("Exceptions sidecar:", result.exceptions_path)
    print("Source audit sidecar:", result.source_audit_path)
    print("Layout:", result.summary.get("output_layout"))
    print("Currency:", result.summary.get("currency"))
    print("Adapter:", result.summary.get("adapter_status"))
    print("Proportion headers:", result.summary.get("proportion_headers"))
    print("Premium rows:", len(result.premium_rows))
    print("Claims rows:", len(result.claims_rows))
    print("Outstanding rows:", len(result.outstanding_rows))
    print("Exceptions:", len(result.exceptions))
    inv = result.summary.get("sheet_inventory") or {}
    if inv:
        print("Sheets:")
        for sn, n in inv.items():
            print(f"  {sn}: {n} rows")
    # Keep SUMMARY reconciliation out of the huge JSON dump
    dump = {k: v for k, v in (result.summary or {}).items() if k != "reconciliation"}
    if result.summary.get("reconciliation"):
        dump["reconciliation_rows"] = len(result.summary["reconciliation"])
    print("Summary:", json.dumps(dump, default=str, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cre_cleaner", description="Continental Re bordereau cleaner")
    sub = p.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run cleaning pipeline for one cedant/broker/quarter")
    run_p.add_argument("--cedant", required=True)
    run_p.add_argument("--broker", required=True)
    run_p.add_argument("--year", type=int, required=True)
    run_p.add_argument("--quarter", type=int, required=True, choices=[1, 2, 3, 4])
    run_p.add_argument("--raw-dir", required=True)
    run_p.add_argument(
        "--template",
        default="templates/TEMPLATE.xlsx",
        help="Layout/style seed workbook (default: templates/TEMPLATE.xlsx)",
    )
    run_p.add_argument("--out-dir", default="output")
    run_p.add_argument("--base-dir", default=None, help="Base directory for relative paths (default: cwd)")
    run_p.add_argument(
        "--collapsed",
        action="store_true",
        help="Write single PREMIUM/CLAIMS/OUTSTANDING sheets (legacy). Default is Bisola-style class-split.",
    )
    run_p.add_argument(
        "--include-audit-sheets",
        action="store_true",
        help="Also embed EXCEPTIONS and SOURCE AUDIT sheets in the cleaned workbook "
             "(default: sidecars only — upload workbook omits them).",
    )
    run_p.add_argument(
        "--include-fac",
        action="store_true",
        help="Include Facultative source sheets / Facultative-* output "
             "(default: ignore FAC — Continental guidance).",
    )
    run_p.add_argument(
        "--proportion-headers",
        choices=["gold", "distinct", "plain"],
        default="gold",
        help="Premium proportion column names. gold (default) = Bisola's most recent "
             "distinct naming (RET/TREATY/FAC PROPORTION %%, as on her Q4 2025 Bond sheet); "
             "distinct = RET PROPORTION %%, TREATY PROPORTION %%, FAC PROPORTION %%; "
             "plain = PROPORTION %% x3 (literal Q2 2025 gold; duplicate names).",
    )
    run_p.add_argument(
        "--out-name",
        default=None,
        help="Output workbook filename (default: {CEDANT}_{BROKER}_{YEAR}_Q{Q}_cleaned.xlsx). "
             "Sidecars follow the same stem.",
    )
    run_p.add_argument(
        "--claims-leading-blank",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="CLAIMS/OUTSTANDING sheets keep column A fully empty with headers in "
             "B..S, title in B1 (TEMPLATE.xlsx / Bisola gold layout; default ON). "
             "Use --no-claims-leading-blank to start claims headers at column A.",
    )
    run_p.set_defaults(func=cmd_run)

    insp = sub.add_parser("inspect", help="Inspect a raw workbook")
    insp.add_argument("--file", required=True)
    insp.set_defaults(func=cmd_inspect)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
