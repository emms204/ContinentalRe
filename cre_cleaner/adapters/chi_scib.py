"""CHI via SCIB — first-pass, unverified.

CHI returns are mostly PDFs plus a few xlsx (PVT, AGRIC, claims, dollar
bordereaux). Discovery matches quarter folders / filenames; parsers reuse
default AIICO-style aliases. PDF files are logged, not read.
"""
from __future__ import annotations

from cre_cleaner.adapters.base import QuarterlyWorkbookAdapter


class ChiScibAdapter(QuarterlyWorkbookAdapter):
    cedant = "CHI"
    broker = "SCIB"
    verified = False
    status_note = (
        "CHI SCIB: Excel files only (many returns are PDF); not checked against Bisola gold"
    )
