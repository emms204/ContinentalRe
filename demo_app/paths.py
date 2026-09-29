"""Fixed paths for the Continental Re demo app."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple

from cre_cleaner.paths import (
    DATA_GOLD,
    OUTPUT_DIR,
    REPO_ROOT,
    SAMPLE_RAW_DIR,
    TEMPLATE_PATH,
)

# Re-export for screens that import demo_app.paths
GOLD_ROOT = DATA_GOLD
DEMO_RUNS_DIR = OUTPUT_DIR / "demo_runs"
LAST_UPLOAD_ZIP = DEMO_RUNS_DIR / "last_upload.zip"  # legacy cache (multi-file era)
LAST_UPLOAD_FILE = DEMO_RUNS_DIR / "last_upload.xlsx"  # Phase 1: single Excel
LAST_UPLOAD_NAME = DEMO_RUNS_DIR / "last_upload_name.txt"
LAST_FORM_JSON = DEMO_RUNS_DIR / "last_form.json"
LAST_RUN_JSON = DEMO_RUNS_DIR / "last_run.json"

DEFAULT_CEDANT = "AIICO"
DEFAULT_BROKER = "ARK"
DEFAULT_YEAR = 2025
DEFAULT_QUARTER = 2

FALLBACK_CLEANED = OUTPUT_DIR / "AIICO_ARK_2025_Q2_cleaned.xlsx"

# AIICO_ARK_2020_Q2_cleaned.xlsx  (or without _cleaned)
_CLEANED_RE = re.compile(
    r"^(?P<cedant>[A-Za-z0-9]+)_(?P<broker>[A-Za-z0-9]+)_(?P<year>\d{4})_Q(?P<quarter>[1-4])",
    re.IGNORECASE,
)


def cleaned_name(cedant: str, broker: str, year: int, quarter: int) -> str:
    return f"{cedant.upper()}_{broker.upper()}_{year}_Q{quarter}_cleaned.xlsx"


def _gold_folder(cedant: str, broker: str) -> str:
    """gold/AIICO_ARK, gold/ROYAL_EXCHANGE_DIRECT, … — spaces → underscores."""
    c = cedant.upper().strip().replace(" ", "_")
    b = broker.upper().strip().replace(" ", "_")
    return f"{c}_{b}"


def gold_dir(cedant: str = DEFAULT_CEDANT, broker: str = DEFAULT_BROKER) -> Path:
    return GOLD_ROOT / _gold_folder(cedant, broker)


def gold_path(
    year: int,
    quarter: int,
    *,
    cedant: str = DEFAULT_CEDANT,
    broker: str = DEFAULT_BROKER,
) -> Path:
    """Canonical Bisola gold: data/gold/{CEDANT}_{BROKER}/{year}/Q{n}_{year}_BORDEREAU_NEW.xlsx.

    Falls back to ``.xlsm``, then currency-split ``_DOMESTIC`` / ``_FOREIGN``
    variants when Bisola only supplied those.
    """
    folder = gold_dir(cedant, broker) / str(year)
    stem = f"Q{quarter}_{year}_BORDEREAU_NEW"
    for suffix in (".xlsx", ".xlsm", ".xls"):
        candidate = folder / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    for tag in ("DOMESTIC", "FOREIGN"):
        for suffix in (".xlsx", ".xlsm", ".xls"):
            candidate = folder / f"{stem}_{tag}{suffix}"
            if candidate.is_file():
                return candidate
    return folder / f"{stem}.xlsx"


def default_gold_path() -> Path:
    return gold_path(DEFAULT_YEAR, DEFAULT_QUARTER)


def parse_cleaned_meta(
    cleaned: Optional[Path],
) -> Optional[Tuple[str, str, int, int]]:
    """Return (cedant, broker, year, quarter) from a cleaned workbook path."""
    if cleaned is None:
        return None
    stem = Path(cleaned).stem
    if stem.endswith("_cleaned"):
        stem = stem[: -len("_cleaned")]
    m = _CLEANED_RE.match(stem)
    if not m:
        return None
    return (
        m.group("cedant").upper(),
        m.group("broker").upper(),
        int(m.group("year")),
        int(m.group("quarter")),
    )


def resolve_gold_for_cleaned(cleaned: Optional[Path]) -> Path:
    """Gold path matching the cleaned file's year/quarter — never a different quarter."""
    meta = parse_cleaned_meta(cleaned)
    if meta is not None:
        cedant, broker, year, quarter = meta
        return gold_path(year, quarter, cedant=cedant, broker=broker)
    return default_gold_path()
