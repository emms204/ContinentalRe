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
    s = s.replace(",", "").replace(" ", "")
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


def parse_period(val: Any) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Split 'dd/mm/yyyy - dd/mm/yyyy' (or 'to') into FROM/TO."""
    if val is None or val == "":
        return None, None
    if isinstance(val, (datetime, date)):
        d = parse_date(val)
        return d, d
    s = clean_text(val)
    m = _PERIOD_SPLIT.match(s)
    if not m:
        d = parse_date(s)
        return d, None
    return parse_date(m.group(1)), parse_date(m.group(2))


def normalize_header(h: Any) -> str:
    s = clean_text(h).upper()
    s = s.replace("\n", " ")
    s = re.sub(r"[^A-Z0-9%]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
