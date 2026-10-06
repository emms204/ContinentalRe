"""AIICO rules shared by the AIICO adapters (ARK and SCIB) — explicitly.

IMPL-20260929-04: every AIICO-specific rule that used to live in the shared
core (map_columns / quarterly / detect / class_labels / table_type / config)
lives here and reaches the engine only through adapter hooks. Only AIICO
adapters inherit :class:`AiicoRulesMixin`; no other cedant sees these rules
(Emmanuel: cedant logic must not be merged).

Evidence
--------
* Numbered PPN bands (IMPL-20260929-01): AIICO ARK premium tabs label treaty
  layers PPN1 / PPN2 / TREATY PPN1 / SUM INSURED2 / PREMIUM1; a PPN2 under a
  misaligned band row (July 2021 'ENG 2ND SURP': TREATY label one column
  right of PPN2) is a treaty layer, not a second retention block.
* RTNTN / RTN% retention abbreviations (RTNTN PPN, RTNTN SUM INSURED).
* Claims: 'AIICO S NET LIABILITY' is the retention amount; source claims
  carry no PPN columns and Bisola's gold fills PPN RET/TREATY/FAC as
  amount / total claims — reproduced by :meth:`derive_claims_ppn` and
  flagged 'Calculated' in the exceptions sidecar (Manual, Claims step 8).
* 2025 Q2 '2ND SURPLUS TREATY' Eng paid: UW YEAR / DESCRIPTION swapped.
* Tabs: AIICO SCIB 'marine os claims' (OS token, O/S), 'MISC. ACC PAID
  JANUARY' (PAID without CLAIM).
* Class labels: GEN ACCIENT-style typos, CAS / CASUALTY / HOUSEHOLDERS
  (Bisola-approved IMPL-20260925-01) and claims CLASS subclasses → General
  Accident.
* Proportion headers: gold survey (24 Bisola AIICO/ARK files, 2020-2025):
  most class sheets use a plain "PROPORTION %" three times, but Bisola's
  Bond - PREMIUM sheets use the distinct RET/TREATY/FAC PROPORTION % naming —
  most recently in Q4 2025; per brief the most recent gold distinct naming
  wins, so the "gold" mode (the default) == that naming.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from cre_cleaner.core.detect import SheetTypeRules
from cre_cleaner.core.map_columns import PremiumLayoutRules, is_share_header
from cre_cleaner.core.normalize import clean_text
from cre_cleaner.core.table_type import TableVocab

# Band qualifiers seen in front of numbered allocation headers
# (RTNTN PPN, TREATY PPN1, TREATY SUM INSURED 1, TREATY PREMIUM2, QUOTA SHARE PPN).
_BAND_PREFIX = r"(?:(?:TREATY|RTNTN|RETENTION|QUOTA SHARE|QUOTA|QS|SURPLUS|SURP|FAC|FACULTATIVE) )?"
_NUMBERED_PPN_RE = re.compile(_BAND_PREFIX + r"PPN ?\d+ ?%?")
_NUMBERED_SI_RE = re.compile(_BAND_PREFIX + r"SUM INSURED ?\d+")
_NUMBERED_PREM_RE = re.compile(_BAND_PREFIX + r"PREMIUM ?\d+")


def is_numbered_ppn(norm: str) -> bool:
    """PPN1 / PPN2 / TREATY PPN1: a numbered treaty-layer share column."""
    return bool(norm) and bool(_NUMBERED_PPN_RE.fullmatch(norm))


class AiicoPremiumRules(PremiumLayoutRules):
    """Generic band vocabulary + AIICO numbered / RTNTN headers."""

    PPN_EXACT = PremiumLayoutRules.PPN_EXACT | {"RTN%", "RTNTN PPN"}
    PPN_PCT_BAND_WORDS = ("RTN",) + PremiumLayoutRules.PPN_PCT_BAND_WORDS
    SI_EXACT = PremiumLayoutRules.SI_EXACT | {
        "RTNTN SUM INSURED", "OUR RETENTION SUM INSURED", "OBLIG TREATY SUM INSURED",
    }
    PREM_EXACT = PremiumLayoutRules.PREM_EXACT | {
        "RTNTN PREMIUM", "OUR RETENTION PREMIUM", "OBLIG TREATY PREMIUM", "PREMIUM1",
    }
    RET_BAND_WORDS = PremiumLayoutRules.RET_BAND_WORDS + ("RTNTN", "RTN%")

    def is_ppn_header(self, norm: str) -> bool:
        if not norm:
            return False
        if is_numbered_ppn(norm) or re.fullmatch(r"PPN\d*", norm):
            return True
        return super().is_ppn_header(norm)

    def looks_like_si(self, norm: str) -> bool:
        return super().looks_like_si(norm) or bool(_NUMBERED_SI_RE.fullmatch(norm))

    def looks_like_prem(self, norm: str) -> bool:
        if not norm or is_share_header(norm):
            return False
        return super().looks_like_prem(norm) or bool(_NUMBERED_PREM_RE.fullmatch(norm))

    def adjust_band(self, kind: str, label: str, ppn_norm: str, ret_set: bool) -> Tuple[str, str]:
        # PPN2 under a misaligned band row is a treaty layer, not a 2nd retention.
        if kind == "ret" and ret_set and is_numbered_ppn(ppn_norm):
            return "sur", "TREATY"
        return kind, label


AIICO_PREMIUM_RULES = AiicoPremiumRules()

AIICO_SHEET_RULES = SheetTypeRules(
    ost_tokens=frozenset({"OST", "OS"}), ost_slash=True, paid_without_claim=True,
)

AIICO_TABLE_VOCAB = TableVocab(share_terms=("RTNTN",), band_terms=("RTNTN",))

AIICO_CLAIMS_ALIAS_EXTRA: Dict[str, List[str]] = {
    # Placed after the generic retention names (same order as before -04).
    "amount_ret": ["AIICO S NET LIABILITY", "AIICOS NET LIABILITY"],
}

AIICO_CLASS_EXTRA: Dict[str, str] = {
    # Obvious General Accident typos / abbreviations seen in raw tabs
    # (e.g. Q3 2020 'GEN ACCIENT').
    "GEN ACCIENT": "General Accident",
    "GENERAL ACCIENT": "General Accident",
    "GEN ACCDT": "General Accident",
    "GEN ACDNT": "General Accident",
    "GEN ACCIDNT": "General Accident",
    # Bisola-approved mappings (IMPL-20260925-01), every quarter:
    # CAS / CASUALTY / HOUSEHOLDERS tabs & rows -> General Accident.
    "CASUALTY": "General Accident",
    "CAS": "General Accident",
    "HOUSEHOLDERS": "General Accident",
    # AIICO ARK claims CLASS column subclasses → General Accident
    "GOODS IN TRANSIT": "General Accident",
    "GIT": "General Accident",
    "ALL RISKS": "General Accident",
    "ALL RISK": "General Accident",
    "BURGLARY": "General Accident",
    "MONEY": "General Accident",
    "FIDELITY GUARANTEE": "General Accident",
    "FIDELITY": "General Accident",
    "PUBLIC LIABILITY": "General Accident",
    "PUBLIC / PRODUCT LIABILITY": "General Accident",
    "PUBLIC/PRODUCT LIABILITY": "General Accident",
    "PRODUCT LIABILITY": "General Accident",
    "PROFESSIONAL INDEMNITY": "General Accident",
    "DIRECTORS AND OFFICERS LIABILITY": "General Accident",
    "D&O": "General Accident",
    "PERSONAL ACCIDENT": "General Accident",
    "WORKMEN COMPENSATION": "General Accident",
    "WORKMEN'S COMPENSATION": "General Accident",
}
# 'CAS' is too short to match safely inside other words (CASH, MCAS...).
AIICO_CLASS_EXACT_ONLY = frozenset({"CAS"})


def aiico_ppn_share(amount: Any, total: Any) -> Any:
    """Bisola-style share of total: amount / total_claims (fraction, not %).

    AIICO source claims rarely ship PPN columns; AIICO gold fills PPN
    RET/TREATY/FAC % from the amount bands. Missing amount → 0 when total is
    usable (matches gold's blank FAC band). Zero/missing total → blank.
    Every value is a *calculated* figure (flagged by the parser).
    """
    if total is None:
        return None
    try:
        denom = float(total)
    except (TypeError, ValueError):
        return None
    if denom == 0:
        return None
    if amount is None:
        return 0.0
    try:
        return float(amount) / denom
    except (TypeError, ValueError):
        return None


def aiico_fix_uw_details(uw: Any, details: Any) -> Optional[Tuple[int, str]]:
    """2025 Q2 '2ND SURPLUS TREATY' Eng paid: UW YR holds loss text and
    DETAILS holds a year. Swap back only in that unambiguous case."""
    if (
        isinstance(uw, str) and len(uw.strip()) > 6 and not uw.strip().isdigit()
        and isinstance(details, str) and details.strip().isdigit()
        and 1950 <= int(details.strip()) <= 2100
    ):
        return int(details.strip()), clean_text(uw)
    return None


class AiicoRulesMixin:
    """Hook values for AIICO adapters (ARK, SCIB). Mixed in explicitly."""

    premium_layout_rules = AIICO_PREMIUM_RULES
    sheet_type_rules = AIICO_SHEET_RULES
    # AIICO gold fills claims PPN from the amount bands (flagged Calculated).
    settings = {"claims_ppn_calculated": True}
    table_vocab = AIICO_TABLE_VOCAB
    claims_alias_extra = AIICO_CLAIMS_ALIAS_EXTRA
    class_label_extra = AIICO_CLASS_EXTRA
    class_label_exact_only = AIICO_CLASS_EXACT_ONLY

    def derive_claims_ppn(self, amount: Any, total: Any) -> Any:
        return aiico_ppn_share(amount, total)

    def fix_claims_uw_details(self, uw: Any, details: Any) -> Optional[Tuple[int, str]]:
        return aiico_fix_uw_details(uw, details)
