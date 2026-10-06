"""Include/exclude transaction rows; exception logging helpers."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from src.domain.cre_cleaner.config import SKIP_TOKENS
from src.domain.cre_cleaner.core.normalize import clean_text, parse_number, as_text_id


def is_blank_row(row: Sequence[Any]) -> bool:
    return not any(clean_text(c) for c in row if c is not None)


def is_nil_row(row: Sequence[Any]) -> bool:
    vals = [clean_text(c).upper() for c in row if c is not None and clean_text(c)]
    if not vals:
        return True
    # majority NIL
    nils = sum(1 for v in vals if v in SKIP_TOKENS or v == "NIL")
    return nils >= max(2, len(vals) - 1) and "NIL" in vals


_TOTAL_TOKENS = (
    "TOTAL", "SUBTOTAL", "GRAND TOTAL", "TOTALS", "BROUGHT FORWARD", "CARRIED FORWARD",
)
# Whole-cell labels that mark a total/footer row even when placed in the insured column.
_TOTAL_LABELS = {
    "TOTAL", "TOTALS", "SUBTOTAL", "SUB TOTAL", "SUB-TOTAL", "GRAND TOTAL",
    "BROUGHT FORWARD", "CARRIED FORWARD",
}


def _key_text(row: Sequence[Any], idx: Optional[int]) -> str:
    return clean_text(row[idx]) if idx is not None and idx < len(row) else ""


def has_transaction_identifiers(row: Sequence[Any], key_cols: Sequence[Optional[int]]) -> bool:
    """Real insured AND a real policy no (or claim no, key_cols[2]) on the row.

    The identifier must be text, not a bare number (a misplaced amount/count),
    and the insured cell must not itself be a total label.
    """
    if len(key_cols) < 2:
        return False
    insured = _key_text(row, key_cols[0])
    if not insured or insured.upper().rstrip(" :") in _TOTAL_LABELS:
        return False
    for idx in key_cols[1:3]:
        ident = _key_text(row, idx)
        if ident and parse_number(ident) is None:
            return True
    return False


def looks_like_total_row(row: Sequence[Any], key_cols: Sequence[Optional[int]]) -> bool:
    """Total rows often have only a numeric in the last amount col and blank insured/policy.

    key_cols = [insured_col, policy_col(, claim_no_col)]. A row carrying a real
    insured plus policy/claim no is a transaction, never a total row — even when
    its narrative says 'TOTAL LOSS' / 'TOTAL SPILLAGE'.
    """
    labels = []
    for c in row:
        t = clean_text(c).upper()
        if t:
            labels.append(t)
    if any(tok in " ".join(labels) for tok in _TOTAL_TOKENS):
        if not has_transaction_identifiers(row, key_cols):
            return True
    # insured/policy empty but some number present → likely total/footer
    insured = None
    policy = None
    if len(key_cols) >= 2:
        insured = _key_text(row, key_cols[0])
        policy = _key_text(row, key_cols[1])
    if not insured and not policy:
        nums = [parse_number(c) for c in row]
        if any(n is not None for n in nums):
            # could be month separator with a date only — handled elsewhere
            non_null = sum(1 for c in row if clean_text(c))
            if non_null <= 2:
                return True
    return False


def looks_like_section_header(row: Sequence[Any]) -> bool:
    vals = [clean_text(c) for c in row if clean_text(c)]
    if not vals:
        return True
    if len(vals) == 1:
        v = vals[0].upper()
        if v in {
            "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
            "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
            "FIRE", "ENGINEERING", "MARINE", "BOND",
        }:
            return True
        # lone datetime month banner
        return True  # single-cell rows are rarely transactions
    return False


def has_min_transaction_evidence(
    insured: Any,
    policy: Any,
    amount: Any,
    extra_amount: Any = None,
) -> bool:
    """Keep row if (policy or insured) AND at least one amount is present (incl. zero/negative)."""
    has_id = bool(as_text_id(insured) or as_text_id(policy))
    a = parse_number(amount)
    b = parse_number(extra_amount) if extra_amount is not None else None
    has_amt = a is not None or b is not None
    return has_id and has_amt
