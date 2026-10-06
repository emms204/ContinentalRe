"""Sheet type detection and header-row detection."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, FrozenSet, List, Optional, Sequence, Tuple

from src.domain.cre_cleaner.core.normalize import normalize_header, clean_text


PREMIUM_MARKERS = {
    "INSURED", "POLICY NO", "POLICY NUMBER", "SUM INSURED", "PREMIUM",
    "GROSS PREMIUM", "PERIOD OF INSURANCE", "DEBIT NOTE",
}
PAID_MARKERS = {
    "CLAIM NO", "CLAIM NUMBER", "DATE OF LOSS", "LOSS DATE",
    "TOTAL CLAIMS", "TOTAL CLAIMS PAID", "PAID CLAIMS",
}
OST_MARKERS = {
    "OST RESERVE", "OUTSTANDING", "TOTAL OST", "OWN SHARE",
}
NIL_LIKE = {"NIL", "N/A", "NA", "NONE", "-"}


@dataclass(frozen=True)
class SheetTypeRules:
    """Tab-name typing vocabulary. Generic by default; a cedant adapter
    supplies its own (``BaseAdapter.sheet_type_rules``) for its tab naming.

    ost_tokens:            whole-word tokens that mark an outstanding tab.
    ost_slash:             'O/S' in the tab name marks an outstanding tab.
    dump_tokens:           tokens of production / listing dumps (not
                           bordereaux) — such tabs are skipped unless PREM.
    paid_without_claim:    a 'PAID' tab name is a paid-claims tab even
                           without the word CLAIM.
    """
    ost_tokens: FrozenSet[str] = field(default_factory=lambda: frozenset({"OST"}))
    ost_slash: bool = False
    dump_tokens: FrozenSet[str] = field(default_factory=frozenset)
    paid_without_claim: bool = False


GENERIC_SHEET_RULES = SheetTypeRules()


def _row_headers(row: Sequence[Any]) -> List[str]:
    return [normalize_header(c) for c in row if c is not None and clean_text(c)]


def score_header_row(headers: List[str], markers: set) -> int:
    score = 0
    joined = " | ".join(headers)
    for m in markers:
        if any(m in h for h in headers) or m in joined:
            score += 1
    return score


def find_header_row(
    rows: List[List[Any]],
    sheet_type: str = "premium",
    max_scan: int = 25,
) -> Optional[int]:
    """Return 0-based header row index, or None."""
    if sheet_type == "premium":
        markers = PREMIUM_MARKERS
        must = {"INSURED", "POLICY"}
    elif sheet_type == "outstanding":
        markers = PAID_MARKERS | OST_MARKERS
        must = {"CLAIM", "POLICY"}
    else:
        markers = PAID_MARKERS
        must = {"CLAIM", "POLICY"}

    best_i, best_score = None, 0
    for i, row in enumerate(rows[:max_scan]):
        headers = _row_headers(row)
        if len(headers) < 3:
            continue
        sc = score_header_row(headers, markers)
        joined = " ".join(headers)
        if not any(m in joined for m in must):
            continue
        # Prefer rows that look like column labels (shortish cells)
        if sc > best_score:
            best_score = sc
            best_i = i
    return best_i if best_score >= 2 else None


def detect_sheet_type(
    sheet_name: str, sample_rows: List[List[Any]], rules: Optional[SheetTypeRules] = None,
) -> str:
    """Return 'premium' | 'paid' | 'outstanding' | 'skip'."""
    rules = rules or GENERIC_SHEET_RULES
    name = normalize_header(sheet_name)
    toks = set(re.findall(r"[A-Z0-9]+", name))
    # Whole-word outstanding tokens only (substring "OS" appears inside LOSS,
    # CROSS, …); the token set comes from the rules.
    is_ost_name = (
        "OUTSTANDING" in name
        or bool(toks & rules.ost_tokens)
        or (rules.ost_slash and bool(re.search(r"\bO\s*/\s*S\b", str(sheet_name).upper())))
    )
    if is_ost_name and (
        "CLAIM" in name or "LOSS" in name or "RESERVE" in name or not ("PREMIUM" in name)
    ):
        # "os claims" / "outstanding" / bare "FIRE OS" → outstanding
        if "PREMIUM" not in name:
            return "outstanding"
    if "OUTSTANDING" in name or (("OUT" in name or "OST" in name) and "CLAIM" in name):
        return "outstanding"
    # Production / listing dumps are not premium bordereaux (adapter vocabulary).
    if (toks & rules.dump_tokens) and "PREM" not in name:
        return "skip"
    # Paid tabs named without the word CLAIM (adapter opt-in)
    if rules.paid_without_claim and "PAID" in name and not is_ost_name:
        return "paid"
    if "CLAIM" in name and "PREMIUM" not in name and "CESSION" not in name:
        if is_ost_name or "OUT" in name or "OST" in name:
            return "outstanding"
        return "paid"
    if "PREMIUM" in name and "CLAIM" not in name:
        return "premium"
    # FAC OBLIG UY premium cession sheets
    if "CESSION" in name or "FACULTATIVE OBLIGATORY PREMIUM" in " ".join(
        normalize_header(c) for r in sample_rows[:5] for c in r if c
    ):
        return "premium"

    # Score from content
    hdr = find_header_row(sample_rows, "premium")
    if hdr is not None:
        headers = _row_headers(sample_rows[hdr])
        joined = " ".join(headers)
        if "CLAIM" in joined:
            if any(x in joined for x in ("OST", "OUTSTANDING", "OWN SHARE", "RESERVE")):
                return "outstanding"
            return "paid"
        if "PREMIUM" in joined or "SUM INSURED" in joined:
            return "premium"

    # Class premium sheets often named MARINE HULL 1ST / FIRE 2ND / BOND
    if any(x in name for x in (
        "HULL", "FIRE", "ENG", "CARGO", "MCARGO", "MISC", "BOND", "ACCIDENT",
        "ACDNT", "SURP", "MOTOR", "AGRIC", "PVT", "TERROR", "AVIATION", "TRAVEL",
    )) and "CLAIM" not in name:
        return "premium"

    return "skip"


def class_from_sheet_name(sheet_name: str) -> str:
    raw = clean_text(sheet_name).upper()
    if "FAC" in raw and "OBLIG" in raw:
        return "FACULTATIVE"
    name = raw
    # Strip surplus / paid / outstanding noise
    for tok in (
        "PAID CLAIMS", "PAID CLAIM", "OUTSTANDING CLAIMS", "OUTSTANDING CLAIM",
        "OUT. CLAIM", "OUT CLAIM", "2ND SURPLUS", "1ST SURPLUS", "2ND SURP",
        "1ST SURP", "TREATY", "FAC OBLIG.", "FAC OBLIG", "FACULTATIVE", "AS AT",
        "FOR THE", "MONTH OF", "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY",
        "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
    ):
        name = name.replace(tok, " ")
    name = re_sub_spaces(name)
    mapping = [
        ("MARINE HULL", "MARINE HULL"),
        ("MHULL", "MARINE HULL"),
        ("HULL", "MARINE HULL"),  # e.g. "HULL 1ST" sheet names
        ("MARINE CARGO", "MARINE CARGO"),
        ("MCARGO", "MARINE CARGO"),
        ("MISC", "MISCELLANEOUS ACCIDENT"),
        ("FIRE", "FIRE"),
        ("ENG", "ENGINEERING"),
        ("BOND", "BOND"),
    ]
    # Whole words only (IMPL-20261006-03 B4): a tab named PASSENGER is not
    # ENGINEERING. A word may start with the key ("FIRE1ST", "BONDS",
    # "MISCELLANEOUS", "ENGINEERING", "ENGINERRING"); multi-word keys must
    # appear as consecutive whole words.
    words = [t for t in re.split(r"[^A-Z0-9]+", name) if t]
    for key, label in mapping:
        if _sheet_class_key_matches(key, name, words):
            return label
    if not name or name in {"2ND", "1ST", "."}:
        return ""
    # Tab names that only describe the bordereau ("CLAIMS PAID", "PREMIUM",
    # "Sheet1") carry no class; class then comes from row CLASS / banners.
    toks = [t for t in re.split(r"[^A-Z0-9]+", name) if t]
    if all(t in _NON_CLASS_TAB_WORDS or _NON_CLASS_TAB_PATTERN.fullmatch(t) for t in toks):
        return ""
    return clean_text(sheet_name)


def _sheet_class_key_matches(key: str, name: str, words: List[str]) -> bool:
    if " " in key:
        return re.search(r"(?<![A-Z0-9])" + re.escape(key) + r"(?![A-Z])", name) is not None
    if key == "ENG":
        return any(w == "ENG" or w.startswith(("ENGIN", "ENGR", "ENGG")) or
                   re.fullmatch(r"ENG\d.*", w) is not None for w in words)
    return any(w.startswith(key) for w in words)


_NON_CLASS_TAB_WORDS = {
    "PREMIUM", "PREMIUMS", "PREM", "CLAIM", "CLAIMS", "PAID", "OUTSTANDING", "OUT",
    "OST", "OS", "LOSS", "LOSSES", "BORDEREAU", "BORDEREAUX", "BORDERAUX", "BORD",
    "BORDREAUX", "BOEREAUX", "BOARDEREUX", "BOADEREUX", "RETURNS", "RETURN",
    "CESSION", "TTY", "QUARTER", "QTR", "AND", "N", "SHEET", "NIL", "SCHEDULE",
    "RESERVE", "RESERVES", "FOR", "THE", "OF", "SUMMARY", "RECOVERY", "PROD",
    "PRODUCTION", "LISTING", "CONFIRMATION",
}
_NON_CLASS_TAB_PATTERN = re.compile(r"\d+|Q[1-4]|\d+(ST|ND|RD|TH)|SHEET\d+")


def re_sub_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
