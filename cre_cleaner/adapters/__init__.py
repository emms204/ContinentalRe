"""Cedant/broker-specific adapters.

No silent fallback: an unknown pair raises UnsupportedCedantError so callers
never inherit another cedant's discovery rules by accident.
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Type

from cre_cleaner.adapters.aiico_ark import AiicoArkAdapter
from cre_cleaner.adapters.base import BaseAdapter, UnsupportedCedantError
from cre_cleaner.adapters.chi_scib import ChiScibAdapter
from cre_cleaner.adapters.custodian_scib import CustodianScibAdapter
from cre_cleaner.adapters.mutual_benefits_ark import MutualBenefitsArkAdapter
from cre_cleaner.adapters.nem_scib import NemScibAdapter
from cre_cleaner.adapters.new_cedants import (
    AiicoAgricDirectAdapter,
    AiicoScibAdapter,
    AxaDirectAdapter,
    CustodianUaibAdapter,
    HeirsDirectAdapter,
    HeirsHibAdapter,
    HeirsJomolaAdapter,
    LasacoFeybilAdapter,
    LasacoJomolaAdapter,
    LasacoJordansAdapter,
    MutualBenefitsJomolaAgricAdapter,
    NemAonAdapter,
    RoyalExchangeAgricAdapter,
    UnitrustAgricAdapter,
    UnitrustArkAdapter,
)
from cre_cleaner.adapters.royal_exchange import RoyalExchangeAdapter

ADAPTERS: Dict[Tuple[str, str], Type[BaseAdapter]] = {
    ("AIICO", "ARK"): AiicoArkAdapter,
    ("AIICO", "SCIB"): AiicoScibAdapter,
    ("AIICO", "AGRIC DIRECT"): AiicoAgricDirectAdapter,
    ("AXA", "DIRECT"): AxaDirectAdapter,
    ("CHI", "SCIB"): ChiScibAdapter,
    ("CUSTODIAN", "SCIB"): CustodianScibAdapter,
    ("CUSTODIAN", "UAIB"): CustodianUaibAdapter,
    ("HEIRS", "DIRECT"): HeirsDirectAdapter,
    ("HEIRS", "HIB"): HeirsHibAdapter,
    ("HEIRS", "JOMOLA"): HeirsJomolaAdapter,
    ("LASACO", "FEYBIL"): LasacoFeybilAdapter,
    ("LASACO", "JOMOLA"): LasacoJomolaAdapter,
    ("LASACO", "JORDANS"): LasacoJordansAdapter,
    ("MUTUAL BENEFITS", "ARK"): MutualBenefitsArkAdapter,
    ("MUTUAL BENEFITS", "JOMOLA AGRIC"): MutualBenefitsJomolaAgricAdapter,
    ("NEM", "AON"): NemAonAdapter,
    ("NEM", "SCIB"): NemScibAdapter,
    ("ROYAL EXCHANGE", "DIRECT"): RoyalExchangeAdapter,
    ("ROYAL EXCHANGE", "AGRIC"): RoyalExchangeAgricAdapter,
    ("UNITRUST", "ARK"): UnitrustArkAdapter,
    ("UNITRUST", "AGRIC"): UnitrustAgricAdapter,
}

# Common aliases so the demo / API can accept folder / shortened names.
_ALIASES: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("MUTUAL", "ARK"): ("MUTUAL BENEFITS", "ARK"),
    ("MUTUALBENEFITS", "ARK"): ("MUTUAL BENEFITS", "ARK"),
    ("MUTUAL BENEFITS", "MUTUAL BENEFITS & ARK"): ("MUTUAL BENEFITS", "ARK"),
    ("MUTUAL BENEFITS", "MUTUAL BENEFIT JOMOLA AGRIC"): ("MUTUAL BENEFITS", "JOMOLA AGRIC"),
    ("REX", "DIRECT"): ("ROYAL EXCHANGE", "DIRECT"),
    ("ROYALEXCHANGE", "DIRECT"): ("ROYAL EXCHANGE", "DIRECT"),
    ("CUSTODIAN AND ALLIED", "SCIB"): ("CUSTODIAN", "SCIB"),
    ("CUSTODIAN AND ALLIED INS", "SCIB"): ("CUSTODIAN", "SCIB"),
    ("CUSTODIAN AND ALLIED INS", "UAIB"): ("CUSTODIAN", "UAIB"),
    ("CHI 2", "SCIB"): ("CHI", "SCIB"),
    ("AXA MANSARD", "DIRECT"): ("AXA", "DIRECT"),
    ("AXA", "AXA"): ("AXA", "DIRECT"),
    ("UNITRUST", "UNITRUST & ARK"): ("UNITRUST", "ARK"),
    ("AIICO", "AGRIC"): ("AIICO", "AGRIC DIRECT"),
}


def list_adapters() -> List[Tuple[str, str, bool, str]]:
    """(cedant, broker, verified, status_note) for UI / API."""
    out = []
    for (c, b), cls in sorted(ADAPTERS.items()):
        inst = cls()
        out.append((c, b, bool(inst.verified), inst.status_note or ""))
    return out


def get_adapter(cedant: str, broker: str) -> BaseAdapter:
    key = (cedant.upper().strip(), broker.upper().strip())
    key = _ALIASES.get(key, key)
    cls = ADAPTERS.get(key)
    if cls is None:
        known = ", ".join(f"{c}/{b}" for c, b in ADAPTERS)
        raise UnsupportedCedantError(
            f"No adapter for cedant={cedant!r} broker={broker!r}. "
            f"Registered: {known}. Add a first-pass adapter rather than falling back."
        )
    return cls()
