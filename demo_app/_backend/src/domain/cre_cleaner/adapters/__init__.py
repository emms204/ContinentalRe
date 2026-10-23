"""Cedant/broker-specific adapters.

No silent fallback: an unknown pair raises UnsupportedCedantError so callers
never inherit another cedant's discovery rules by accident.
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Type

from src.domain.cre_cleaner.adapters.aiico_ark import AiicoArkAdapter
from src.domain.cre_cleaner.adapters.base import BaseAdapter, UnsupportedCedantError
from src.domain.cre_cleaner.adapters.chi_scib import ChiScibAdapter
from src.domain.cre_cleaner.adapters.custodian_scib import CustodianScibAdapter
from src.domain.cre_cleaner.adapters.emple import EmpleAonAdapter, EmpleScibAdapter
from src.domain.cre_cleaner.adapters.mutual_benefits_ark import MutualBenefitsArkAdapter
from src.domain.cre_cleaner.adapters.nem_scib import NemScibAdapter
from src.domain.cre_cleaner.adapters.prestige import (
    PrestigeAfroAsianAdapter,
    PrestigeFairAdapter,
    PrestigeIbnAdapter,
    PrestigeJbBodaAdapter,
    PrestigeJomolaAdapter,
    PrestigeKmdAdapter,
    PrestigeScibAdapter,
)
from src.domain.cre_cleaner.adapters.nsia import NsiaArkAdapter, NsiaJordansAdapter
from src.domain.cre_cleaner.adapters.new_cedants import (
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
from src.domain.cre_cleaner.adapters.royal_exchange import RoyalExchangeAdapter
from src.domain.cre_cleaner.adapters.zenith import ZenithArkAdapter, ZenithJordansAdapter

from src.domain.cre_cleaner.adapters.cornerstone import CornerstoneScibAdapter, CornerstoneJordansAdapter, CornerstoneHilalAdapter
from src.domain.cre_cleaner.adapters.leadway import LeadwayArkAdapter, LeadwayAgricAdapter, LeadwayScibAdapter, LeadwayUaibAdapter, LeadwayYoaAdapter
from src.domain.cre_cleaner.adapters.linkage import LinkageAgricAdapter, LinkageJordansAdapter
from src.domain.cre_cleaner.adapters.sanlam import SanlamArkAdapter, SanlamAgricAdapter, SanlamScibAdapter, SanlamJordansAdapter
from src.domain.cre_cleaner.adapters.sunu import SunuUaibAdapter

ADAPTERS: Dict[Tuple[str, str], Type[BaseAdapter]] = {
    ('CORNERSTONE', 'SCIB'): CornerstoneScibAdapter,
    ('CORNERSTONE', 'JORDANS'): CornerstoneJordansAdapter,
    ('CORNERSTONE', 'HILAL'): CornerstoneHilalAdapter,
    ('LEADWAY', 'ARK'): LeadwayArkAdapter,
    ('LEADWAY', 'AGRIC'): LeadwayAgricAdapter,
    ('LEADWAY', 'SCIB'): LeadwayScibAdapter,
    ('LEADWAY', 'UAIB'): LeadwayUaibAdapter,
    ('LEADWAY', 'YOA'): LeadwayYoaAdapter,
    ('LINKAGE', 'AGRIC'): LinkageAgricAdapter,
    ('LINKAGE', 'JORDANS'): LinkageJordansAdapter,
    ('SANLAM GENERAL INSURANCE', 'ARK'): SanlamArkAdapter,
    ('SANLAM GENERAL INSURANCE', 'AGRIC'): SanlamAgricAdapter,
    ('SANLAM GENERAL INSURANCE', 'SCIB'): SanlamScibAdapter,
    ('SANLAM GENERAL INSURANCE', 'JORDANS'): SanlamJordansAdapter,
    ('SUNU', 'UAIB'): SunuUaibAdapter,
    ("AIICO", "ARK"): AiicoArkAdapter,
    ("AIICO", "SCIB"): AiicoScibAdapter,
    ("AIICO", "AGRIC DIRECT"): AiicoAgricDirectAdapter,
    ("AXA", "DIRECT"): AxaDirectAdapter,
    ("CHI", "SCIB"): ChiScibAdapter,
    ("CUSTODIAN", "SCIB"): CustodianScibAdapter,
    ("CUSTODIAN", "UAIB"): CustodianUaibAdapter,
    ("EMPLE", "SCIB"): EmpleScibAdapter,
    ("EMPLE", "AON"): EmpleAonAdapter,
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
    ("NSIA", "JORDANS"): NsiaJordansAdapter,
    ("NSIA", "ARK"): NsiaArkAdapter,
    ("PRESTIGE", "FAIR"): PrestigeFairAdapter,
    ("PRESTIGE", "IBN"): PrestigeIbnAdapter,
    ("PRESTIGE", "SCIB"): PrestigeScibAdapter,
    ("PRESTIGE", "AFRO ASIAN"): PrestigeAfroAsianAdapter,
    ("PRESTIGE", "JB BODA"): PrestigeJbBodaAdapter,
    ("PRESTIGE", "JOMOLA"): PrestigeJomolaAdapter,
    ("PRESTIGE", "KMD"): PrestigeKmdAdapter,
    ("ROYAL EXCHANGE", "DIRECT"): RoyalExchangeAdapter,
    ("ROYAL EXCHANGE", "AGRIC"): RoyalExchangeAgricAdapter,
    ("UNITRUST", "ARK"): UnitrustArkAdapter,
    ("UNITRUST", "AGRIC"): UnitrustAgricAdapter,
    ("ZENITH", "ARK"): ZenithArkAdapter,
    ("ZENITH", "JORDANS"): ZenithJordansAdapter,
}

# Common aliases so the demo / API can accept folder / shortened names.
_ALIASES: Dict[Tuple[str, str], Tuple[str, str]] = {
    ('CORNERSTONE', 'JORDANS GLOBAL'): ('CORNERSTONE', 'JORDANS'),
    ('LINKAGE', 'JORDANS GLOBAL'): ('LINKAGE', 'JORDANS'),
    ('SANLAM', 'ARK'): ('SANLAM GENERAL INSURANCE', 'ARK'),
    ('ALLIANZ', 'ARK'): ('SANLAM GENERAL INSURANCE', 'ARK'),
    ('ALLIANZ NIGERIA', 'ARK'): ('SANLAM GENERAL INSURANCE', 'ARK'),
    ('SANLAM', 'AGRIC'): ('SANLAM GENERAL INSURANCE', 'AGRIC'),
    ('ALLIANZ', 'AGRIC'): ('SANLAM GENERAL INSURANCE', 'AGRIC'),
    ('ALLIANZ NIGERIA', 'AGRIC'): ('SANLAM GENERAL INSURANCE', 'AGRIC'),
    ('SANLAM', 'SCIB'): ('SANLAM GENERAL INSURANCE', 'SCIB'),
    ('ALLIANZ', 'SCIB'): ('SANLAM GENERAL INSURANCE', 'SCIB'),
    ('ALLIANZ NIGERIA', 'SCIB'): ('SANLAM GENERAL INSURANCE', 'SCIB'),
    ('SANLAM', 'JORDANS'): ('SANLAM GENERAL INSURANCE', 'JORDANS'),
    ('ALLIANZ', 'JORDANS'): ('SANLAM GENERAL INSURANCE', 'JORDANS'),
    ('ALLIANZ NIGERIA', 'JORDANS'): ('SANLAM GENERAL INSURANCE', 'JORDANS'),
    ('SANLAM GENERAL INSURANCE', 'JORDANS GLOBAL'): ('SANLAM GENERAL INSURANCE', 'JORDANS'),
    ('SANLAM', 'JORDANS GLOBAL'): ('SANLAM GENERAL INSURANCE', 'JORDANS'),
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
    ("ZENITH", "JORDANS GLOBAL"): ("ZENITH", "JORDANS"),
    ("NSIA", "JORDANS GLOBAL"): ("NSIA", "JORDANS"),
    ("PRESTIGE", "AFRO-ASIAN"): ("PRESTIGE", "AFRO ASIAN"),
    ("PRESTIGE", "AFROASIAN"): ("PRESTIGE", "AFRO ASIAN"),
    ("PRESTIGE", "J.B. BODA"): ("PRESTIGE", "JB BODA"),
    ("PRESTIGE", "J B BODA"): ("PRESTIGE", "JB BODA"),
    ("PRESTIGE", "K.M. DASTUR"): ("PRESTIGE", "KMD"),
    ("PRESTIGE", "KM DASTUR"): ("PRESTIGE", "KMD"),
    ("PRESTIGE ASSURANCE", "FAIR"): ("PRESTIGE", "FAIR"),
    ("NSIA", "ARK INSURANCE BROKERS"): ("NSIA", "ARK"),
    # Staging cedents rows for EMPLE / NSIA did not exist on 2026-10-21; add
    # their cedents.name spellings here once the partners are created.
    # Staging cedents.name spellings (partnerId resolve)
    ("LASACO", "FEYBILL"): ("LASACO", "FEYBIL"),
    ("MUTUAL BENEFIT", "ARK"): ("MUTUAL BENEFITS", "ARK"),
    ("MUTUAL BENEFIT", "JOMOLA"): ("MUTUAL BENEFITS", "JOMOLA AGRIC"),
    ("MUTUAL BENEFIT", "JOMOLA AGRIC"): ("MUTUAL BENEFITS", "JOMOLA AGRIC"),
    ("UNITTRUST", "ARK"): ("UNITRUST", "ARK"),
    ("UNITTRUST", "AGRIC"): ("UNITRUST", "AGRIC"),
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
