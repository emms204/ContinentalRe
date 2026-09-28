"""NEM via SCIB — first-pass, unverified.

One combined Premium/Loss/Outstanding workbook per quarter (same pattern as
Custodian). Dollar / foreign treaty folders under AON are separate currency
inputs picked up by filename/folder currency detection.
"""
from __future__ import annotations

from cre_cleaner.adapters.base import QuarterlyWorkbookAdapter


class NemScibAdapter(QuarterlyWorkbookAdapter):
    cedant = "NEM"
    broker = "SCIB"
    verified = False
    status_note = (
        "NEM SCIB: quarterly combined Premium/Loss workbooks; not checked against Bisola gold"
    )
