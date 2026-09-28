"""Fixed paths for the Continental Re demo app."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

SAMPLE_RAW_DIR = REPO_ROOT / "AIICO" / "ARK" / "2025"
TEMPLATE_PATH = REPO_ROOT / "TEMPLATE.xlsx"
OUTPUT_DIR = REPO_ROOT / "output"
DEMO_RUNS_DIR = OUTPUT_DIR / "demo_runs"
GOLD_ROOT = REPO_ROOT / "gold"
LAST_UPLOAD_ZIP = DEMO_RUNS_DIR / "last_upload.zip"
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


def gold_dir(cedant: str = DEFAULT_CEDANT, broker: str = DEFAULT_BROKER) -> Path:
    return GOLD_ROOT / f"{cedant.upper()}_{broker.upper()}"


def gold_path(
    year: int,
    quarter: int,
    *,
    cedant: str = DEFAULT_CEDANT,
    broker: str = DEFAULT_BROKER,
) -> Path:
    """Canonical Bisola gold: gold/AIICO_ARK/{year}/Q{n}_{year}_BORDEREAU_NEW.xlsx."""
    return gold_dir(cedant, broker) / str(year) / f"Q{quarter}_{year}_BORDEREAU_NEW.xlsx"


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
