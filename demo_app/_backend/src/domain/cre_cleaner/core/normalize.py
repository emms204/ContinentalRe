"""Normalize dates, negatives, text IDs, and whitespace."""
from __future__ import annotations

import re
from datetime import datetime, date, time
from functools import lru_cache
from typing import Any, Optional, Tuple

# Precompiled forms of the patterns used per cell (IMPL-20261002-03). The
# str-only caches below are pure functions of their argument; results are
# immutable (str / datetime / None), so sharing them is safe.
_WS_RE = re.compile(r"\s+")
_NON_HEADER_CHARS_RE = re.compile(r"[^A-Z0-9%]+")
_STR_CACHE_SIZE = 65536
# Excel date serials read as dates only inside this range (1990-01-01 to
# 2100-01-01); other numbers are amounts, years or counts.
EXCEL_SERIAL_MIN = 32874
EXCEL_SERIAL_MAX = 73050


def is_excel_serial_date(val: Any) -> bool:
    return (isinstance(val, (int, float)) and not isinstance(val, bool)
            and EXCEL_SERIAL_MIN <= val <= EXCEL_SERIAL_MAX)


def format_amount(amount: Any, currency: str = "NGN") -> str:
    """'₦1,234.50' (or 'USD 1,234.50') for WARN details."""
    try:
        value = float(amount or 0.0)
    except (TypeError, ValueError):
        return str(amount)
    code = (currency or "NGN").upper()
    return f"₦{value:,.2f}" if code == "NGN" else f"{code} {value:,.2f}"


def clean_text(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, float) and val == int(val):
        # Avoid 2025.0 style for years accidentally passed as text path
        s = str(int(val))
    else:
        s = str(val)
    s = s.replace("\xa0", " ").replace("\u200b", "")
    s = _WS_RE.sub(" ", s).strip()
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
    if type(val) is str:
        return _parse_date_str(val)
    return _parse_date_any(val)


@lru_cache(maxsize=_STR_CACHE_SIZE)
def _parse_date_str(val: str) -> Optional[datetime]:
    return _parse_date_any(val)


def _parse_date_any(val: Any) -> Optional[datetime]:
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
    s = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r",\s*", ", ", s)
    s = re.sub(r"([A-Za-z])(\d{4})\b", r"\1 \2", s)
    # Python's %b accepts Sep, whereas source returns also use Sept.
    # A whole-token replacement leaves full month names/unrelated text intact.
    s = re.sub(r"\bSEPT\b", "Sep", s, flags=re.IGNORECASE)
    try:
        d = datetime.fromisoformat(s)
        return datetime(d.year, d.month, d.day)
    except ValueError:
        pass
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
            return datetime.strptime(s, fmt)
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
        return parse_date(val), None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return (parse_date(val) if is_excel_serial_date(val) else None), None
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
_FOREIGN_CURRENCY_RE = re.compile(r"\bFOREIGN[\s_-]+CURRENC(?:Y|IES)\b")
# Insurer system exports: "Currency Filter ( NAIRA at 1 )" is authoritative;
# the report title "… FOREIGN CURRENCY" is a template label, not the book currency.
_CURRENCY_FILTER_RE = re.compile(
    r"CURRENCY\s*FILTER\s*\(\s*([A-Za-z]+)",
    re.IGNORECASE,
)


def currency_codes(text: Any) -> set:
    """Currency codes named in a filename / sheet name / header cell.

    Explicit FCY / "foreign currency" is kept as its own code: the source
    does not say which currency, so it must not be assumed to be USD.
    "Foreign treaty" describes the treaty, not the currency of its amounts.
    """
    s = clean_text(text).upper()
    if not s:
        return set()
    # A counterparty name is not currency evidence. Keep standalone Sterling,
    # Pound Sterling, symbols and explicit codes (including elsewhere in the cell).
    s = re.sub(r"\bSTERLING(?=\s+(?:ASSURANCE|INSURANCE|BANK|COMPANY)\b)", "", s)
    found = {code for sym, code in _CURRENCY_SYMBOLS if sym in s}
    for tok in re.split(r"[^A-Z]+", s):
        if tok != "FOREIGN" and tok in _CURRENCY_TOKENS:
            found.add(_CURRENCY_TOKENS[tok])
    if _FOREIGN_CURRENCY_RE.search(s):
        found.add("FCY")
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
    if s == "FOREIGN":
        # Here the value is explicitly in a CURRENCY column, rather than
        # appearing incidentally in a treaty/report heading.
        return "FCY"
    codes = prefer_named_over_fcy(currency_codes(s))
    return codes.pop() if len(codes) == 1 else ""


def normalize_header(h: Any) -> str:
    if type(h) is str:
        return _normalize_header_str(h)
    return _normalize_header_any(h)


@lru_cache(maxsize=_STR_CACHE_SIZE)
def _normalize_header_str(h: str) -> str:
    return _normalize_header_any(h)


def _normalize_header_any(h: Any) -> str:
    s = clean_text(h).upper()
    s = s.replace("\n", " ")
    s = _NON_HEADER_CHARS_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s
