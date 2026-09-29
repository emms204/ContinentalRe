"""Repository layout paths (single source of truth).

ContinentalRe/
  cre_cleaner/     package (adapters/, core/, io/, entrypoints)
  demo_app/        Streamlit UI
  tests/
  templates/       TEMPLATE.xlsx
  docs/            reports / notes
  data/raw/        newdata, next5 (source bordereaux)
  data/gold/       Bisola cleaned reference
  output/          run artifacts (gitignored)
"""
from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

TEMPLATES_DIR = REPO_ROOT / "templates"
TEMPLATE_PATH = TEMPLATES_DIR / "TEMPLATE.xlsx"

DOCS_DIR = REPO_ROOT / "docs"

DATA_DIR = REPO_ROOT / "data"
DATA_RAW = DATA_DIR / "raw"
DATA_NEWDATA = DATA_RAW / "newdata"
DATA_NEXT5 = DATA_RAW / "next5"
DATA_GOLD = DATA_DIR / "gold"

OUTPUT_DIR = REPO_ROOT / "output"

# Phase 1 demo sample (AIICO ARK monthly premiums under newdata)
SAMPLE_RAW_DIR = DATA_NEWDATA / "AIICO" / "ARK" / "2025"
