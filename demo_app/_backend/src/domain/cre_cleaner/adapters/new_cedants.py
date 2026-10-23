"""First-pass adapters for NewData + Next 5 Cedant pairs.

Each cedant/broker reuses QuarterlyWorkbookAdapter (or month-file discovery)
with the generic rules plus only its own cedant's hooks (per-cedant modules:
aiico_common, axa, heirs, lasaco, unitrust). Layouts differ — these are unverified until checked against
Bisola gold. PDF sources are converted upstream via ``pdf_extract`` before
discovery runs.
"""
from __future__ import annotations

from src.domain.cre_cleaner.adapters.aiico_common import AiicoRulesMixin
from src.domain.cre_cleaner.adapters.axa import AxaDirectAdapter  # noqa: F401  (re-export)
from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.adapters.custodian_common import CustodianLayoutMixin
from src.domain.cre_cleaner.adapters.custodian_uaib import CustodianUaibClaimsMixin
from src.domain.cre_cleaner.adapters.heirs import (  # noqa: F401  (re-export)
    HeirsDirectAdapter, HeirsHibAdapter, HeirsJomolaAdapter,
)
from src.domain.cre_cleaner.adapters.lasaco import (  # noqa: F401  (re-export)
    LasacoFeybilAdapter, LasacoJomolaAdapter, LasacoJordansAdapter,
)
from src.domain.cre_cleaner.adapters.monthly_files import MonthlyPremiumAdapter
from src.domain.cre_cleaner.adapters.mutual_benefits_ark import MUTUAL_CLAIMS_ALIAS_EXTRA
from src.domain.cre_cleaner.adapters.nem_common import NemClaimsMixin, NemPremiumMixin
from src.domain.cre_cleaner.adapters.nem_scib import NEM_CLAIMS_TOTAL_FALLBACK
from src.domain.cre_cleaner.adapters.royal_exchange import (
    RoyalExchangeClaimsMixin,
    RoyalExchangePremiumRules,
)
from src.domain.cre_cleaner.adapters.unitrust import (  # noqa: F401  (re-export)
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
    settings = {
        "claims_ppn_calculated": True,
        "single_class": "Agriculture",
        "period_source": "label",
    }

    verified = False
    status_note = (
        "AIICO Agric Direct: quarterly treaty returns workbooks; not checked against Bisola gold"
    )


class CustodianUaibAdapter(CustodianUaibClaimsMixin, CustodianLayoutMixin, QuarterlyWorkbookAdapter):
    """UAIB uses class-only outstanding tabs with an explicit reporting banner."""

    cedant = "CUSTODIAN"
    broker = "UAIB"
    content_sheet_typing = True
    claims_alias_extra = {
        "period_to": ["END DATE"],
        "ppn_treaty": ["TTY %", "TREATY SHARE PPN"],
        "details": ["CLAIMS DETAIL", "LOSS DETAILS"],
    }
    # Keep the generic explicit total headers ahead of these layout-specific
    # fallbacks, and never use a paid amount as the outstanding reserve.
    claims_total_fallback = {
        "paid": ("CLAIM AMOUNT", "AMOUNT"),
        "outstanding": ("LOSS AMOUNT",),
    }

    def claims_exclude(self, field: str, norm: str) -> bool:
        # The 2021 paid layout labels its 100% claim amount simply "Amount".
        # Do not let fuzzy matching consume it as "Retention Amount" before
        # the type-specific total fallback can use it.
        return norm == "AMOUNT" and field in {"amount_ret", "amount_treaty", "amount_fac"}

    verified = False
    status_note = "Custodian/UAIB: quarterly combined workbooks; unverified"


class NemAonAdapter(NemClaimsMixin, NemPremiumMixin, QuarterlyWorkbookAdapter):
    cedant = "NEM"
    broker = "AON"
    claims_total_fallback = NEM_CLAIMS_TOTAL_FALLBACK
    verified = False
    status_note = "NEM/AON: quarterly (incl. dollar) returns; unverified"


class MutualBenefitsJomolaAgricAdapter(QuarterlyWorkbookAdapter):
    cedant = "MUTUAL BENEFITS"
    broker = "JOMOLA AGRIC"
    settings = {"single_class": "Agriculture"}
    claims_alias_extra = MUTUAL_CLAIMS_ALIAS_EXTRA
    verified = False
    status_note = "Mutual Benefits Jomola Agric: quarterly returns; unverified"


class RoyalExchangeAgricAdapter(RoyalExchangeClaimsMixin, QuarterlyWorkbookAdapter):
    premium_layout_rules = RoyalExchangePremiumRules()
    cedant = "ROYAL EXCHANGE"
    broker = "AGRIC"
    settings = {"single_class": "Agriculture"}
    verified = False
    status_note = "Royal Exchange Agric: quarterly returns; unverified"
