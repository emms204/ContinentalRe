"""LASACO adapters (Feybil, Jomola, Jordans) — LASACO rules only (IMPL-20260929-04).

LASACO outstanding / premium tabs use a spanning band row with a sub-row::

  row1: … GROSS LOSS RESERVE | RETENTION | | TREATY |
  row2: …                    | %         | AMOUNT | % | AMOUNT

The shared merge mechanism (``core.map_columns.merge_group_subheaders``,
``band_subrow_layout``, on by default) handles it; stated explicitly here.
"""
from __future__ import annotations

from cre_cleaner.adapters.base import QuarterlyWorkbookAdapter


# LASACO premium tabs may carry only the cedant's share columns ('OUR SHARE
# SI' / 'OUR SHARE OF G PREM'): used for TSI / GP only when no policy-level
# SUM INSURED / GROSS PREMIUM column exists (alias order: shared aliases
# first). Moved out of the shared alias table (IMPL-20260929-04).
LASACO_PREMIUM_ALIAS_EXTRA = {
    "sum_insured": ["OUR SHARE SI", "OUR SHARE SUM INSURED"],
    "gross_premium": ["OUR SHARE OF G PREM", "OUR SHARE GROSS PREMIUM"],
}


class _LasacoAdapter(QuarterlyWorkbookAdapter):
    band_subrow_layout = True
    premium_alias_extra = LASACO_PREMIUM_ALIAS_EXTRA


class LasacoJomolaAdapter(_LasacoAdapter):
    cedant = "LASACO"
    broker = "JOMOLA"
    verified = False
    status_note = "LASACO/JOMOLA: class/quarter returns; unverified"


class LasacoFeybilAdapter(_LasacoAdapter):
    cedant = "LASACO"
    broker = "FEYBIL"
    verified = False
    status_note = "LASACO/FEYBIL: quarterly premium/claims bordereaux; unverified"


class LasacoJordansAdapter(_LasacoAdapter):
    cedant = "LASACO"
    broker = "JORDANS"
    verified = False
    status_note = "LASACO/JORDANS: quarterly returns; unverified"
