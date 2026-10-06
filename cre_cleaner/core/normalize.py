"""Normalize dates, negatives, text IDs, and whitespace."""
from __future__ import annotations

import re
from datetime import datetime, date, time
from typing import Any, Optional, Tuple


def clean_text(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, float) and val == int(val):
        # Avoid 2025.0 style for years accidentally passed as text path
        s = str(int(val))
    else:
        s = str(val)
    s = s.replace("\xa0", " ").replace("\u200b", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def as_text_id(val: Any) -> str:
    """Keep identifiers as text — no scientific notation / lost leading zeros."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        if val != val:  # NaN
            return ""
        if abs(val) >= 1e15:
            # likely corrupted scientific; keep as best-effort integer string
            return format(val, ".0f")
        if val == int(val):
            return str(int(val))
        return str(val).rstrip("0").rstrip(".") if "." in str(val) else str(val)
    s = clean_text(val)
    # Strip trailing .0 from stringified floats
    if re.fullmatch(r"-?\d+\.0+", s):
        s = s.split(".")[0]
    return s


def parse_number(val: Any) -> Optional[float]:
    """Parse numeric while preserving negatives. Never invent."""
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        if isinstance(val, float) and val != val:
            return None
        return float(val)
    s = clean_text(val)
    if not s or s.upper() in {"NIL", "N/A", "NA", "-", "—"}:
        return None
    # Accounting negatives (1,234.56) or (1234.56)
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    # Trailing minus used in some bordereaux: "1,234-" / "1234.50-"
    if re.search(r"[\d.]-\s*$", s) and not s.startswith("-"):
        neg = True
        s = s.rstrip().rstrip("-").rstrip()
    # Currency codes / symbols (₦1,234.56 / NGN 1,000.00 / US$500)
    s = re.sub(
        r"^(?:NGN|USD|EUR|GBP|FCY|NAIRA|DOLLAR|DOLLARS|US\$)\s*",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = s.lstrip("₦$€£")
    s = s.replace(",", "").replace(" ", "").replace("\u00a0", "")
    if s.endswith("%"):
        s = s[:-1]
    try:
        n = float(s)
        return -n if neg else n
    except ValueError:
        return None


def parse_date(val: Any) -> Optional[datetime]:
    """Parse to datetime (date only). Keep as real date for Excel."""
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return datetime(val.year, val.month, val.day)
    if isinstance(val, date) and not isinstance(val, datetime):
        return datetime(val.year, val.month, val.day)
    if isinstance(val, (int, float)):
        # Excel serial — leave to openpyxl normally; try common path via epoch
        try:
            from openpyxl.utils.datetime import from_excel
            d = from_excel(val)
            if isinstance(d, datetime):
                return datetime(d.year, d.month, d.day)
            if isinstance(d, date):
                return datetime(d.year, d.month, d.day)
        except Exception:
            return None
    s = clean_text(val)
    if not s or s.upper() in {"NIL", "N/A", "NA"}:
        return None
    # Drop trailing time if present
    for fmt in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%Y-%m-%d",
        "%d/%m/%y",
        "%d-%m-%y",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%d %b %Y",
        "%d %B %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(s[:20].strip(), fmt)
        except ValueError:
            continue
    return None


_PERIOD_SPLIT = re.compile(
    r"(.+?)\s*(?:-|–|—|to|TO)\s*(.+)",
    re.IGNORECASE,
)


_PERIOD_SEP = re.compile(r"\s*(?:-|–|—|\bto\b)\s*", re.IGNORECASE)


def parse_period(val: Any) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Split 'dd/mm/yyyy - dd/mm/yyyy' (or 'to') into FROM/TO.

    A lone date (Excel serial or string) sets FROM only — TO stays blank.
    Never copy the same date into both ends.
    """
    if val is None or val == "":
        return None, None
    if isinstance(val, (datetime, date)):
        d = parse_date(val)
        return d, None
    s = clean_text(val)
    # A lone date may itself contain dashes ('01-Jan-2025'); try it whole first.
    d = parse_date(s)
    if d is not None:
        return d, None
    # Then every separator position ('01-Jan-2025 - 31-Mar-2025' splits at the
    # middle dash only when both halves are dates).
    for m in _PERIOD_SEP.finditer(s):
        a, b = parse_date(s[: m.start()]), parse_date(s[m.end():])
        if a is not None and b is not None:
            return a, b
    m = _PERIOD_SPLIT.match(s)
    if not m:
        return None, None
    return parse_date(m.group(1)), parse_date(m.group(2))


_CURRENCY_TOKENS = {
    "USD": "USD", "DOLLAR": "USD", "DOLLARS": "USD",
    "NGN": "NGN", "NAIRA": "NGN",
    "EUR": "EUR", "EURO": "EUR", "EUROS": "EUR",
    "GBP": "GBP", "STERLING": "GBP",
    "FCY": "FCY", "FOREIGN": "FCY",
}
_CURRENCY_SYMBOLS = (("US$", "USD"), ("$", "USD"), ("₦", "NGN"), ("€", "EUR"), ("£", "GBP"))
_NAMED_CURRENCIES = frozenset({"NGN", "USD", "EUR", "GBP"})
# Insurer system exports: "Currency Filter ( NAIRA at 1 )" is authoritative;
# the report title "… FOREIGN CURRENCY" is a template label, not the book currency.
_CURRENCY_FILTER_RE = re.compile(
    r"CURRENCY\s*FILTER\s*\(\s*([A-Za-z]+)",
    re.IGNORECASE,
)


def currency_codes(text: Any) -> set:
    """Currency codes named in a filename / sheet name / header cell.

    FCY ("foreign currency") is kept as its own code: the source does not say
    which currency, so it must not be assumed to be USD.
    """
    s = clean_text(text).upper()
    if not s:
        return set()
    found = {code for sym, code in _CURRENCY_SYMBOLS if sym in s}
    for tok in re.split(r"[^A-Z]+", s):
        if tok in _CURRENCY_TOKENS:
            found.add(_CURRENCY_TOKENS[tok])
    return found


def currency_filter_code(text: Any) -> str:
    """Named currency from a ``Currency Filter ( NAIRA … )`` style cell, else ''."""
    s = clean_text(text)
    if not s:
        return ""
    m = _CURRENCY_FILTER_RE.search(s)
    if not m:
        return ""
    tok = m.group(1).upper()
    if tok in {"NONE", "NIL", "N", "NA"}:
        return ""
    return _CURRENCY_TOKENS.get(tok, "")


def prefer_named_over_fcy(codes: set) -> set:
    """If a real ISO currency is present, drop vague FCY/FOREIGN labels.

    Some report titles say ``FOREIGN CURRENCY`` even when ``Currency Filter`` is
    NAIRA; keeping both caused premium→NGN and claims→FCY splits.
    """
    if not codes:
        return codes
    named = codes & _NAMED_CURRENCIES
    if named:
        return named
    return set(codes)


def currency_code(text: Any) -> str:
    """Single code for a CURRENCY cell (e.g. 'USD', 'N', 'Naira'), '' if unknown."""
    s = clean_text(text).upper()
    if s in {"N", "NGN", "₦"}:
        return "NGN"
    codes = prefer_named_over_fcy(currency_codes(s))
    return codes.pop() if len(codes) == 1 else ""


def normalize_header(h: Any) -> str:
    s = clean_text(h).upper()
    s = s.replace("\n", " ")
    s = re.sub(r"[^A-Z0-9%]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
