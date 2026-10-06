"""CHI via SCIB — first-pass, unverified.

CHI returns are mostly PDFs plus a few xlsx (PVT, AGRIC, claims, dollar
bordereaux). Discovery matches quarter folders / filenames; parsers reuse
default AIICO-style aliases. PDFs are converted to Excel via LlamaParse
before discovery (see ``cre_cleaner.io.pdf``).
"""
from __future__ import annotations

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter


class ChiScibAdapter(QuarterlyWorkbookAdapter):
    cedant = "CHI"
    broker = "SCIB"
    verified = False
    status_note = (
        "CHI SCIB: PDF→xlsx via LlamaParse then quarterly discovery; "
        "not checked against Bisola gold"
    )
