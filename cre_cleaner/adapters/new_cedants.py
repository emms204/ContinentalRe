"""First-pass adapters for NewData + Next 5 Cedant pairs.

Each cedant/broker reuses QuarterlyWorkbookAdapter (or AIICO monthly discovery)
with default aliases. Layouts differ — these are unverified until checked against
Bisola gold. PDF sources are converted upstream via ``pdf_extract`` before
discovery runs.
"""
from __future__ import annotations

from cre_cleaner.adapters.aiico_ark import AiicoArkAdapter
from cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from cre_cleaner.adapters.mutual_benefits_ark import MutualBenefitsArkAdapter


class AiicoScibAdapter(AiicoArkAdapter):
    cedant = "AIICO"
    broker = "SCIB"
    verified = False
    status_note = (
        "AIICO SCIB: same monthly/quarterly discovery as ARK; not checked against Bisola gold"
    )


class AiicoAgricDirectAdapter(QuarterlyWorkbookAdapter):
    cedant = "AIICO"
    broker = "AGRIC DIRECT"
    verified = False
    status_note = (
        "AIICO Agric Direct: quarterly treaty returns workbooks; not checked against Bisola gold"
    )


class AxaDirectAdapter(QuarterlyWorkbookAdapter):
    cedant = "AXA"
    broker = "DIRECT"
    verified = False
    status_note = (
        "AXA Mansard: quarterly premium/claims Excel + PDFs (via LlamaParse); "
        "not checked against Bisola gold"
    )


class HeirsJomolaAdapter(QuarterlyWorkbookAdapter):
    cedant = "HEIRS"
    broker = "JOMOLA"
    verified = False
    status_note = "HEIRS/JOMOLA: quarterly treaty cession workbooks; unverified"


class HeirsHibAdapter(QuarterlyWorkbookAdapter):
    cedant = "HEIRS"
    broker = "HIB"
    verified = False
    status_note = "HEIRS/HIB: quarterly returns; unverified"


class HeirsDirectAdapter(QuarterlyWorkbookAdapter):
    cedant = "HEIRS"
    broker = "DIRECT"
    verified = False
    status_note = "HEIRS Direct: quarterly returns; unverified"


class LasacoJomolaAdapter(QuarterlyWorkbookAdapter):
    cedant = "LASACO"
    broker = "JOMOLA"
    verified = False
    status_note = "LASACO/JOMOLA: class/quarter returns; unverified"


class LasacoFeybilAdapter(QuarterlyWorkbookAdapter):
    cedant = "LASACO"
    broker = "FEYBIL"
    verified = False
    status_note = "LASACO/FEYBIL: quarterly premium/claims bordereaux; unverified"


class LasacoJordansAdapter(QuarterlyWorkbookAdapter):
    cedant = "LASACO"
    broker = "JORDANS"
    verified = False
    status_note = "LASACO/JORDANS: quarterly returns; unverified"


class UnitrustArkAdapter(MutualBenefitsArkAdapter):
    cedant = "UNITRUST"
    broker = "ARK"
    verified = False
    status_note = (
        "Unitrust & ARK: AIICO-style discovery; not checked against Bisola gold"
    )


class UnitrustAgricAdapter(QuarterlyWorkbookAdapter):
    cedant = "UNITRUST"
    broker = "AGRIC"
    verified = False
    status_note = "Unitrust Agric: quarterly returns; unverified"


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
    cedant = "ROYAL EXCHANGE"
    broker = "AGRIC"
    verified = False
    status_note = "Royal Exchange Agric: quarterly returns; unverified"
