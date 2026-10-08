"""Include/exclude transaction rows; exception logging helpers."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional, Sequence

from src.domain.cre_cleaner.config import SKIP_TOKENS
from src.domain.cre_cleaner.core.normalize import (
    as_text_id,
    clean_text,
    parse_date,
    parse_number,
    parse_period,
)


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


# One token, no spaces, at least four digits, and a letter, slash, or hyphen.
# Segments are split on / . - . Bare amounts (7093988.05) fail the letter/slash/
# hyphen lookahead, so a sum sitting in INSURED is not treated as a policy number.
POLICY_RE = re.compile(
    r"^(?=(?:[^0-9]*[0-9]){4})(?=.*[A-Z/-])[A-Z0-9]+(?:[/.-][A-Z0-9]+)*$"
)
DATE_TXT = re.compile(
    r"^\d{4}-\d{2}-\d{2}([ T]00:00:00)?$|^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$"
)


def _filled_cell(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def is_date_cell(value: Any) -> bool:
    """True for a datetime/date cell or text that matches DATE_TXT."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, datetime):
        return True
    if isinstance(value, date):
        return True
    if isinstance(value, (int, float)):
        return False
    return DATE_TXT.match(str(value).strip()) is not None


def is_policy_value(value: Any) -> bool:
    """POLICY_RE on str(value).strip().upper(), length <= 40, and not a date."""
    if value is None or isinstance(value, bool) or isinstance(value, (datetime, date)):
        return False
    text = str(value).strip().upper()
    if not text or len(text) > 40 or DATE_TXT.match(text):
        return False
    return POLICY_RE.match(text) is not None


def _parses_date_or_range(value: Any) -> bool:
    if is_date_cell(value) or parse_date(value) is not None:
        return True
    start, end = parse_period(value)
    return start is not None and end is not None


def row_identity_failures(
    insured_cell: Any,
    policy_cell: Any,
    from_cell: Any,
    to_cell: Any,
) -> set:
    """Raw-cell identity failures.

    F1: INSURED is a policy number or a date.
    F2: POLICY NO is a date or datetime.
    F3: both period cells are filled and neither is a date or a range.
    A row fails the headerless-block trigger only on F1 or F2. F3 is evidence
    that the dates are bad while the identity columns are still in the right place.
    """
    failures = set()
    if _filled_cell(insured_cell) and (
        is_policy_value(insured_cell) or is_date_cell(insured_cell)
    ):
        failures.add("F1")
    if _filled_cell(policy_cell) and is_date_cell(policy_cell):
        failures.add("F2")
    if (
        _filled_cell(from_cell)
        and _filled_cell(to_cell)
        and not _parses_date_or_range(from_cell)
        and not _parses_date_or_range(to_cell)
    ):
        failures.add("F3")
    return failures


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
