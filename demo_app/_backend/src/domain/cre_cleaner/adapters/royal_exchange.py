"""Royal Exchange — first-pass, unverified.

Treaty analysis / bordereaux / reconciliation workbooks per quarter; some
returns are class-specific (e.g. General Accidents) or AGRIC. Discovery is
quarter-token based; sheet typing is left to the parser.
"""
from __future__ import annotations

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.core.map_columns import PremiumLayoutRules


class RoyalExchangePremiumRules(PremiumLayoutRules):
    """Royal Exchange premium tabs label the retention share 'RTN %'
    (IMPL-20260929-04: moved out of the shared rules; same behaviour)."""
    PPN_EXACT = PremiumLayoutRules.PPN_EXACT | {"RTN%"}
    PPN_PCT_BAND_WORDS = ("RTN",) + PremiumLayoutRules.PPN_PCT_BAND_WORDS
    RET_BAND_WORDS = PremiumLayoutRules.RET_BAND_WORDS + ("RTN%",)


class RoyalExchangeAdapter(QuarterlyWorkbookAdapter):
    premium_layout_rules = RoyalExchangePremiumRules()
    cedant = "ROYAL EXCHANGE"
    broker = "DIRECT"
    verified = False
    status_note = (
        "Royal Exchange: quarterly treaty analysis/bordereaux workbooks; "
        "not checked against Bisola gold"
    )
