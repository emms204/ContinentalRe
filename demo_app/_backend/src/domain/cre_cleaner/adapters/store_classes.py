"""Explicit per-cedant map: class-store class name -> template output class
(IMPL-20261006-05).

Store names are the snake_case ``insurance_class.class_name`` values that POST
/load-partner-class writes. Seen in staging (read-only query, 2026-10-06):
agriculture, bond, engineering, fire, general_accident, marine_cargo,
marine_hull, oil_and_energy, terrorism. Every cedant is listed explicitly so
one cedant can differ without touching the others. A store class that is not
listed for the cedant raises WARN ``store_class_unmapped`` and is ignored: it
is never guessed. Keys are canonical adapter cedants (``ADAPTERS``); the map
applies to every broker (partner) of that cedant, but the aliases themselves
are read per partner.
"""
from __future__ import annotations

from typing import Dict, Optional

_TEMPLATE_CLASSES_2026_10: Dict[str, str] = {
    "agriculture": "Agriculture",
    "bond": "Bond",
    "engineering": "Engineering",
    "fire": "Fire",
    "general_accident": "General Accident",
    "marine_cargo": "Marine Cargo",
    "marine_hull": "Marine Hull",
    "oil_and_energy": "Oil & Gas",
    "terrorism": "Terrorism & PVT",
}

STORE_CLASS_TO_TEMPLATE: Dict[str, Dict[str, str]] = {
    "AIICO": dict(_TEMPLATE_CLASSES_2026_10),
    "AXA": dict(_TEMPLATE_CLASSES_2026_10),
    "CHI": dict(_TEMPLATE_CLASSES_2026_10),
    "CUSTODIAN": dict(_TEMPLATE_CLASSES_2026_10),
    "HEIRS": dict(_TEMPLATE_CLASSES_2026_10),
    "LASACO": dict(_TEMPLATE_CLASSES_2026_10),
    "MUTUAL BENEFITS": dict(_TEMPLATE_CLASSES_2026_10),
    "NEM": dict(_TEMPLATE_CLASSES_2026_10),
    "ROYAL EXCHANGE": dict(_TEMPLATE_CLASSES_2026_10),
    "UNITRUST": dict(_TEMPLATE_CLASSES_2026_10),
}


def store_class_map(cedant: Optional[str]) -> Dict[str, str]:
    """The cedant's explicit map (empty = every store class is unmapped)."""
    return dict(STORE_CLASS_TO_TEMPLATE.get(" ".join(str(cedant or "").upper().split()), {}))
