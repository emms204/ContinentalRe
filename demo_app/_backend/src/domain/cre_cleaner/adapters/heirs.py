"""HEIRS adapters (Direct, HIB, Jomola) — HEIRS rules only (IMPL-20260929-04).

* 'RTN%' is the HEIRS retention share column (band = retention).
* HEIRS/JOMOLA submissions include production / listing dumps (tabs named
  PROD / PRODUCTION / LISTING) that are not premium bordereaux.
"""
from __future__ import annotations

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.core.detect import SheetTypeRules
from src.domain.cre_cleaner.core.map_columns import PremiumLayoutRules


class HeirsPremiumRules(PremiumLayoutRules):
    PPN_EXACT = PremiumLayoutRules.PPN_EXACT | {"RTN%"}
    PPN_PCT_BAND_WORDS = ("RTN",) + PremiumLayoutRules.PPN_PCT_BAND_WORDS
    RET_BAND_WORDS = PremiumLayoutRules.RET_BAND_WORDS + ("RTN%",)


HEIRS_PREMIUM_RULES = HeirsPremiumRules()
HEIRS_JOMOLA_SHEET_RULES = SheetTypeRules(
    dump_tokens=frozenset({"PROD", "PRODUCTION", "LISTING"}),
)


class _HeirsAdapter(QuarterlyWorkbookAdapter):
    premium_layout_rules = HEIRS_PREMIUM_RULES


class HeirsJomolaAdapter(_HeirsAdapter):
    cedant = "HEIRS"
    broker = "JOMOLA"
    verified = False
    status_note = "HEIRS/JOMOLA: quarterly treaty cession workbooks; unverified"
    sheet_type_rules = HEIRS_JOMOLA_SHEET_RULES


class HeirsHibAdapter(_HeirsAdapter):
    cedant = "HEIRS"
    broker = "HIB"
    verified = False
    status_note = "HEIRS/HIB: quarterly returns; unverified"


class HeirsDirectAdapter(_HeirsAdapter):
    cedant = "HEIRS"
    broker = "DIRECT"
    verified = False
    status_note = "HEIRS Direct: quarterly returns; unverified"
