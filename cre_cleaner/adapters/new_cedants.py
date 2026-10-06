"""First-pass adapters for NewData + Next 5 Cedant pairs.

Each cedant/broker reuses QuarterlyWorkbookAdapter (or month-file discovery)
with the generic rules plus only its own cedant's hooks (per-cedant modules:
aiico_common, axa, heirs, lasaco, unitrust). Layouts differ — these are unverified until checked against
Bisola gold. PDF sources are converted upstream via ``pdf_extract`` before
discovery runs.
"""
from __future__ import annotations

from cre_cleaner.adapters.aiico_common import AiicoRulesMixin
from cre_cleaner.adapters.axa import AxaDirectAdapter  # noqa: F401  (re-export)
from cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from cre_cleaner.adapters.heirs import (  # noqa: F401  (re-export)
    HeirsDirectAdapter, HeirsHibAdapter, HeirsJomolaAdapter,
)
from cre_cleaner.adapters.lasaco import (  # noqa: F401  (re-export)
    LasacoFeybilAdapter, LasacoJomolaAdapter, LasacoJordansAdapter,
)
from cre_cleaner.adapters.monthly_files import MonthlyPremiumAdapter
from cre_cleaner.adapters.royal_exchange import RoyalExchangePremiumRules
from cre_cleaner.adapters.unitrust import (  # noqa: F401  (re-export)
    UnitrustAgricAdapter, UnitrustArkAdapter,
)


class AiicoScibAdapter(AiicoRulesMixin, MonthlyPremiumAdapter):
    """AIICO via SCIB: AIICO rules through the explicit AIICO helper
    (adapters.aiico_common) + the same month-file discovery as ARK."""

    cedant = "AIICO"
    broker = "SCIB"
    verified = False
    status_note = (
        "AIICO SCIB: same monthly/quarterly discovery as ARK; not checked against Bisola gold"
    )
    content_sheet_typing = True


class AiicoAgricDirectAdapter(QuarterlyWorkbookAdapter):
    """Generic mapping; claims PPN calculated from amounts (flagged
    'Calculated'), as for the other AIICO adapters and as before IMPL-04."""
    cedant = "AIICO"
    broker = "AGRIC DIRECT"
    settings = {"claims_ppn_calculated": True}

    verified = False
    status_note = (
        "AIICO Agric Direct: quarterly treaty returns workbooks; not checked against Bisola gold"
    )


class CustodianUaibAdapter(QuarterlyWorkbookAdapter):
    cedant = "CUSTODIAN"
    broker = "UAIB"
    verified = False
    status_note = "Custodian/UAIB: quarterly combined workbooks; unverified"


class NemAonAdapter(QuarterlyWorkbookAdapter):
    cedant = "NEM"
    broker = "AON"
    verified = False
    status_note = "NEM/AON: quarterly (incl. dollar) returns; unverified"


class MutualBenefitsJomolaAgricAdapter(QuarterlyWorkbookAdapter):
    cedant = "MUTUAL BENEFITS"
    broker = "JOMOLA AGRIC"
    verified = False
    status_note = "Mutual Benefits Jomola Agric: quarterly returns; unverified"


class RoyalExchangeAgricAdapter(QuarterlyWorkbookAdapter):
    premium_layout_rules = RoyalExchangePremiumRules()
    cedant = "ROYAL EXCHANGE"
    broker = "AGRIC"
    verified = False
    status_note = "Royal Exchange Agric: quarterly returns; unverified"
