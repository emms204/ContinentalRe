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
from cre_cleaner.adapters.royal_exchange import RoyalExchangeAdapter

ADAPTERS: Dict[Tuple[str, str], Type[BaseAdapter]] = {
    ("AIICO", "ARK"): AiicoArkAdapter,
    ("CHI", "SCIB"): ChiScibAdapter,
    ("CUSTODIAN", "SCIB"): CustodianScibAdapter,
    ("MUTUAL BENEFITS", "ARK"): MutualBenefitsArkAdapter,
    ("NEM", "SCIB"): NemScibAdapter,
    ("ROYAL EXCHANGE", "DIRECT"): RoyalExchangeAdapter,
}

# Common aliases so the demo / API can accept shortened names.
_ALIASES: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("MUTUAL", "ARK"): ("MUTUAL BENEFITS", "ARK"),
    ("MUTUALBENEFITS", "ARK"): ("MUTUAL BENEFITS", "ARK"),
    ("REX", "DIRECT"): ("ROYAL EXCHANGE", "DIRECT"),
    ("ROYALEXCHANGE", "DIRECT"): ("ROYAL EXCHANGE", "DIRECT"),
    ("CUSTODIAN AND ALLIED", "SCIB"): ("CUSTODIAN", "SCIB"),
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
