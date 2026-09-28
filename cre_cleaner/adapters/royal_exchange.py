"""Royal Exchange — first-pass, unverified.

Treaty analysis / bordereaux / reconciliation workbooks per quarter; some
returns are class-specific (e.g. General Accidents) or AGRIC. Discovery is
quarter-token based; sheet typing is left to the parser.
"""
from __future__ import annotations

from cre_cleaner.adapters.base import QuarterlyWorkbookAdapter


class RoyalExchangeAdapter(QuarterlyWorkbookAdapter):
    cedant = "ROYAL EXCHANGE"
    broker = "DIRECT"
    verified = False
    status_note = (
        "Royal Exchange: quarterly treaty analysis/bordereaux workbooks; "
        "not checked against Bisola gold"
    )
