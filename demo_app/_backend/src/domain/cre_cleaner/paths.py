"""Package-local paths for the vendored cre_cleaner engine.

TEMPLATE.xlsx is an optional style seed under this package. Output workbooks
are still written when the file is absent. GCS I/O for the API lives in
``WorkbookCleanerService``, not here.
"""
from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent

# Style seed only. write_output_workbook still writes when this file is absent.
TEMPLATES_DIR = PACKAGE_DIR / "templates"
TEMPLATE_PATH = TEMPLATES_DIR / "TEMPLATE.xlsx"
