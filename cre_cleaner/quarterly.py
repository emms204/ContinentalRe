"""Parse premium / claims sheets and merge monthly premiums for a quarter."""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from cre_cleaner.detect import (
    detect_sheet_type,
    find_header_row,
    class_from_sheet_name,
)
from cre_cleaner.filters import (
    is_blank_row,
    is_nil_row,
    looks_like_total_row,
    looks_like_section_header,
    has_min_transaction_evidence,
)
from cre_cleaner.io_excel import read_workbook_sheets
from cre_cleaner.map_columns import (
    detect_premium_allocation_blocks,
    map_simple_columns,
    CLAIMS_ALIASES,
    cell,
)
from cre_cleaner.models import (
    PremiumRow,
    ClaimsRow,
    AuditMeta,
    ExceptionRecord,
    SourceAuditRecord,
)
from cre_cleaner.normalize import (
    as_text_id,
    clean_text,
    parse_number,
    parse_date,
    parse_period,
    normalize_header,
)


def parse_premium_file(
    path: Path,
    source_month: str,
) -> Tuple[List[PremiumRow], List[ExceptionRecord], List[SourceAuditRecord]]:
    sheets = read_workbook_sheets(path)
    rows_out: List[PremiumRow] = []
    exceptions: List[ExceptionRecord] = []
    audit: List[SourceAuditRecord] = []

    for sn, raw_rows in sheets.items():
        st = detect_sheet_type(sn, raw_rows[:15])
        if st != "premium":
            audit.append(SourceAuditRecord(
                source_filename=path.name,
                source_sheet=sn,
                sheet_type=st,
                header_row=0,
                rows_read=0,
                rows_kept=0,
                rows_skipped=0,
                source_month=source_month,
                notes="skipped non-premium sheet in premium file",
            ))
            continue

        hdr_i = find_header_row(raw_rows, "premium")
        if hdr_i is None:
            exceptions.append(ExceptionRecord(
                "WARN", "header_not_found", path.name, sn, 0,
                "Could not detect premium header row",
            ))
            audit.append(SourceAuditRecord(
                path.name, sn, "premium", 0, 0, 0, 0, source_month, "no header",
            ))
            continue

        group_row = raw_rows[hdr_i - 1] if hdr_i > 0 else None
        header = raw_rows[hdr_i]
        cmap = detect_premium_allocation_blocks(header, group_row)
        class_hint = class_from_sheet_name(sn)

        kept = 0
        skipped = 0
        read_n = 0
        insured_i = cmap.get("insured")
        policy_i = cmap.get("policy_no")

        for ridx in range(hdr_i + 1, len(raw_rows)):
            row = raw_rows[ridx]
            excel_row = ridx + 1  # 1-based
            if is_blank_row(row):
                continue
            read_n += 1
            if is_nil_row(row):
                skipped += 1
                continue
            if looks_like_total_row(row, [insured_i, policy_i]):
                skipped += 1
                continue
            if looks_like_section_header(row) and not (
                insured_i is not None and clean_text(cell(row, insured_i))
            ):
                skipped += 1
                continue

            insured = as_text_id(cell(row, insured_i)) if insured_i is not None else ""
            # Prefer human name text
            if insured_i is not None:
                insured = clean_text(cell(row, insured_i))
            policy = as_text_id(cell(row, policy_i)) if policy_i is not None else ""
            gross = parse_number(cell(row, cmap.get("gross_premium")))
            si = parse_number(cell(row, cmap.get("sum_insured")))

            if not has_min_transaction_evidence(insured, policy, gross, si):
                # uncertain — log, do not silently drop if has some content
                if insured or policy or gross is not None:
                    exceptions.append(ExceptionRecord(
                        "INFO", "insufficient_evidence_kept_out",
                        path.name, sn, excel_row,
                        f"insured={insured!r} policy={policy!r} gross={gross}",
                    ))
                skipped += 1
                continue

            period_from = period_to = None
            if cmap.get("period") is not None:
                period_from, period_to = parse_period(cell(row, cmap.get("period")))
            else:
                period_from = parse_date(cell(row, cmap.get("period_from")))
                period_to = parse_date(cell(row, cmap.get("period_to")))

            uw = cell(row, cmap.get("uw_year"))
            if isinstance(uw, float) and uw == int(uw):
                uw = int(uw)
            elif isinstance(uw, str) and uw.strip().isdigit():
                uw = int(uw.strip())

            channel = ""
            sub_channel = ""
            if cmap.get("channel") is not None:
                channel = clean_text(cell(row, cmap.get("channel")))
            if cmap.get("sub_channel") is not None:
                # Only use as channel/subchannel if not already used as class elsewhere
                sub_channel = clean_text(cell(row, cmap.get("sub_channel")))

            prow = PremiumRow(
                policy_no=policy,
                name_of_insured=insured,
                channel=channel,
                sub_channel="",  # TEMPLATE has SUB CHANNEL; source SUB CLASS is class-ish — leave blank per design unless CHANNEL present
                underwriting_year=uw,
                period_from=period_from,
                period_to=period_to,
                total_sum_insured=si,
                mpl_pct=parse_number(cell(row, cmap.get("mpl"))),
                gross_premium=gross,
                ret_ppn=parse_number(cell(row, cmap.ret_ppn)),
                ret_si=parse_number(cell(row, cmap.ret_si)),
                ret_prem=parse_number(cell(row, cmap.ret_prem)),
                sur_ppn=parse_number(cell(row, cmap.sur_ppn)),
                sur_si=parse_number(cell(row, cmap.sur_si)),
                sur_prem=parse_number(cell(row, cmap.sur_prem)),
                fac_ppn=parse_number(cell(row, cmap.fac_ppn)),
                fac_si=parse_number(cell(row, cmap.fac_si)),
                fac_prem=parse_number(cell(row, cmap.fac_prem)),
                audit=AuditMeta(
                    source_month=source_month,
                    source_filename=path.name,
                    source_sheet=sn,
                    source_row=excel_row,
                    class_hint=class_hint,
                ),
            )
            # If required field missing — leave blank, log, still include
            if not policy:
                exceptions.append(ExceptionRecord(
                    "WARN", "missing_policy_no", path.name, sn, excel_row, insured,
                ))
            rows_out.append(prow)
            kept += 1

        audit.append(SourceAuditRecord(
            source_filename=path.name,
            source_sheet=sn,
            sheet_type="premium",
            header_row=hdr_i + 1,
            rows_read=read_n,
            rows_kept=kept,
            rows_skipped=skipped,
            source_month=source_month,
            notes=f"class_hint={class_hint}",
        ))

    return rows_out, exceptions, audit


def merge_monthly_premiums(
    month_files: Sequence[Tuple[int, Path, str]],
) -> Tuple[List[PremiumRow], List[ExceptionRecord], List[SourceAuditRecord]]:
    """month_files: (month_num, path, month_label) in calendar order."""
    all_rows: List[PremiumRow] = []
    all_exc: List[ExceptionRecord] = []
    all_audit: List[SourceAuditRecord] = []
    for _m, path, label in month_files:
        rows, exc, audit = parse_premium_file(path, label)
        all_rows.extend(rows)
        all_exc.extend(exc)
        all_audit.extend(audit)
    return all_rows, all_exc, all_audit


def _parse_claims_sheet(
    path: Path,
    sn: str,
    raw_rows: List[List[Any]],
    sheet_type: str,
) -> Tuple[List[ClaimsRow], List[ExceptionRecord], SourceAuditRecord]:
    exceptions: List[ExceptionRecord] = []
    hdr_i = find_header_row(raw_rows, "outstanding" if sheet_type == "outstanding" else "paid")
    class_hint = class_from_sheet_name(sn)

    if hdr_i is None:
        exceptions.append(ExceptionRecord(
            "WARN", "header_not_found", path.name, sn, 0, f"type={sheet_type}",
        ))
        return [], exceptions, SourceAuditRecord(
            path.name, sn, sheet_type, 0, 0, 0, 0, "", "no header",
        )

    # Some sheets repeat headers (e.g. FAC paid with monthly sections). Collect all header indices.
    header_indices = [hdr_i]
    for i in range(hdr_i + 1, len(raw_rows)):
        norms = [normalize_header(c) for c in raw_rows[i] if c is not None]
        joined = " ".join(norms)
        if "CLAIM" in joined and "POLICY" in joined and ("DATE" in joined or "LOSS" in joined or "S NO" in joined or "S/N" in joined.replace(" ", "")):
            if sum(1 for n in norms if n) >= 5:
                header_indices.append(i)

    rows_out: List[ClaimsRow] = []
    kept = skipped = read_n = 0
    # Persist across repeated monthly headers (e.g. 2ND SURPLUS TREATY: FIRE banner
    # then several month headers without re-stating the class).
    section_class = class_hint

    _SECTION_BANNER = {
        "FIRE", "ENGINEERING", "ENG", "MARINE HULL", "MARINE CARGO", "MARINE",
        "MISCELLANEOUS", "MISC", "BOND", "MOTOR", "ACCIDENT",
    }
    _SECTION_MAP = {
        "ENG": "ENGINEERING",
        "MISC": "MISCELLANEOUS ACCIDENT",
        "MISCELLANEOUS": "MISCELLANEOUS ACCIDENT",
        "MARINE": "MARINE",
    }

    def _banner_class(cell_text: str) -> str:
        b = cell_text.upper().strip()
        if b in _SECTION_BANNER:
            return _SECTION_MAP.get(b, b)
        return ""

    for hi, header_at in enumerate(header_indices):
        header = raw_rows[header_at]
        cmap = map_simple_columns(header, CLAIMS_ALIASES)
        end = header_indices[hi + 1] if hi + 1 < len(header_indices) else len(raw_rows)
        insured_i = cmap.get("insured")
        policy_i = cmap.get("policy_no")
        # Look further back for a lone class banner above this header
        for back in range(1, 20):
            bi = header_at - back
            if bi < 0:
                break
            ne = [clean_text(c) for c in raw_rows[bi] if clean_text(c)]
            if len(ne) == 1:
                mapped = _banner_class(ne[0])
                if mapped:
                    section_class = mapped
                    break
            # Stop if we hit a previous header row
            if bi in header_indices:
                break

        for ridx in range(header_at + 1, end):
            row = raw_rows[ridx]
            excel_row = ridx + 1
            if is_blank_row(row):
                continue
            ne = [clean_text(c) for c in row if clean_text(c)]
            if len(ne) == 1:
                mapped = _banner_class(ne[0])
                if mapped:
                    section_class = mapped
                    skipped += 1
                    continue
            # skip repeated header
            norms = [normalize_header(c) for c in row if c is not None]
            if norms and normalize_header(norms[0] if False else (row[0] if row else "")) in {"S NO", "S/N", "S/NO"} or (
                "CLAIM NO" in " ".join(norms) and "POLICY" in " ".join(norms)
            ):
                # another header — skip
                if "CLAIM" in " ".join(norms) and "POLICY" in " ".join(norms):
                    skipped += 1
                    continue
            read_n += 1
            if is_nil_row(row):
                skipped += 1
                continue
            if looks_like_total_row(row, [insured_i, policy_i]):
                skipped += 1
                continue
            # month banners / lone dates
            non_empty = [c for c in row if clean_text(c)]
            if len(non_empty) <= 1:
                skipped += 1
                continue

            insured = clean_text(cell(row, insured_i)) if insured_i is not None else ""
            policy = as_text_id(cell(row, policy_i)) if policy_i is not None else ""
            claim_no = as_text_id(cell(row, cmap.get("claim_no"))) if cmap.get("claim_no") is not None else ""
            total = parse_number(cell(row, cmap.get("total_claims")))
            amt_ret = parse_number(cell(row, cmap.get("amount_ret")))
            amt_tr = parse_number(cell(row, cmap.get("amount_treaty")))
            amt_fac = parse_number(cell(row, cmap.get("amount_fac")))

            if not has_min_transaction_evidence(insured, policy, total, amt_ret if amt_ret is not None else amt_tr):
                if insured or policy or claim_no:
                    exceptions.append(ExceptionRecord(
                        "INFO", "insufficient_evidence_kept_out",
                        path.name, sn, excel_row,
                        f"insured={insured!r} claim={claim_no!r}",
                    ))
                skipped += 1
                continue

            # class: prefer column, else sheet hint
            class_name = ""
            if cmap.get("class") is not None:
                class_name = clean_text(cell(row, cmap.get("class")))
            if not class_name:
                class_name = section_class or class_hint

            period_from = period_to = None
            if cmap.get("period") is not None:
                period_from, period_to = parse_period(cell(row, cmap.get("period")))
            else:
                period_from = parse_date(cell(row, cmap.get("period_from")))
                period_to = parse_date(cell(row, cmap.get("period_to")))

            uw = cell(row, cmap.get("uw_yr"))
            if isinstance(uw, float) and uw == int(uw):
                uw = int(uw)

            details = ""
            if cmap.get("details") is not None:
                details = clean_text(cell(row, cmap.get("details")))

            crow = ClaimsRow(
                insured=insured,
                class_name=class_name,
                policy_no=policy,
                claim_no=claim_no,
                date_of_loss=parse_date(cell(row, cmap.get("date_of_loss"))),
                uw_yr=uw,
                period_from=period_from,
                period_to=period_to,
                total_claims=total,
                ppn_ret=None,  # rarely in AIICO ARK claims
                amount_ret=amt_ret,
                ppn_treaty=None,
                amount_treaty=amt_tr,
                ppn_fac=None,
                amount_fac=amt_fac,
                details=details,
                audit=AuditMeta(
                    source_filename=path.name,
                    source_sheet=sn,
                    source_row=excel_row,
                    class_hint=class_hint,
                ),
            )
            rows_out.append(crow)
            kept += 1

    return rows_out, exceptions, SourceAuditRecord(
        source_filename=path.name,
        source_sheet=sn,
        sheet_type=sheet_type,
        header_row=(hdr_i + 1) if hdr_i is not None else 0,
        rows_read=read_n,
        rows_kept=kept,
        rows_skipped=skipped,
        source_month="",
        notes=f"class_hint={class_hint}",
    )


def parse_claims_file(
    path: Path,
) -> Tuple[List[ClaimsRow], List[ClaimsRow], List[ExceptionRecord], List[SourceAuditRecord]]:
    """Return (paid_rows, outstanding_rows, exceptions, audit). Never merge paid+OST."""
    sheets = read_workbook_sheets(path)
    paid: List[ClaimsRow] = []
    ost: List[ClaimsRow] = []
    exceptions: List[ExceptionRecord] = []
    audit: List[SourceAuditRecord] = []

    for sn, raw_rows in sheets.items():
        st = detect_sheet_type(sn, raw_rows[:20])
        if st == "skip":
            # statement sheet etc.
            name_u = normalize_header(sn)
            if "STATEMENT" in name_u:
                audit.append(SourceAuditRecord(
                    path.name, sn, "skip", 0, 0, 0, 0, "", "statement/cover skipped",
                ))
                continue
            # try content
            st2 = detect_sheet_type(sn, raw_rows[:30])
            st = st2
        if st == "premium":
            # FAC OBLIG UY premium cession inside claims workbook — skip for claims pipeline
            audit.append(SourceAuditRecord(
                path.name, sn, "premium", 0, 0, 0, 0, "",
                "premium cession sheet inside claims file — not merged into claims",
            ))
            continue
        if st not in {"paid", "outstanding"}:
            audit.append(SourceAuditRecord(
                path.name, sn, st, 0, 0, 0, 0, "", "unclassified skip",
            ))
            continue

        rows, exc, aud = _parse_claims_sheet(path, sn, raw_rows, st)
        exceptions.extend(exc)
        audit.append(aud)
        if st == "paid":
            paid.extend(rows)
        else:
            ost.extend(rows)

    return paid, ost, exceptions, audit
