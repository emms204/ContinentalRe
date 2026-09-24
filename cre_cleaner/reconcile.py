"""Counts + sum checks → SUMMARY / variances."""
from __future__ import annotations

from typing import List, Sequence

from cre_cleaner.models import PremiumRow, ClaimsRow, ExceptionRecord
from cre_cleaner.normalize import parse_number


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

    # Flag apparent duplicate claim keys (do not delete)
    seen = {}
    for r in list(claims_rows) + list(outstanding_rows):
        key = (r.claim_no or "", r.policy_no or "", str(r.date_of_loss), r.total_claims)
        if not r.claim_no:
            continue
        if key in seen:
            # caller may already have exceptions; we add note count in summary only
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


def flag_duplicate_claims(rows: Sequence[ClaimsRow], source_label: str) -> List[ExceptionRecord]:
    """Flag apparent duplicates — never auto-delete."""
    from collections import defaultdict
    buckets = defaultdict(list)
    for i, r in enumerate(rows):
        if not r.claim_no and not r.policy_no:
            continue
        key = (r.claim_no, r.policy_no, str(r.date_of_loss), r.total_claims)
        buckets[key].append(i)
    out = []
    for key, idxs in buckets.items():
        if len(idxs) > 1:
            out.append(ExceptionRecord(
                severity="WARN",
                reason="apparent_duplicate",
                source_filename=source_label,
                source_sheet="",
                source_row=idxs[0],
                detail=f"key={key} count={len(idxs)} indices={idxs[:10]}",
            ))
    return out
