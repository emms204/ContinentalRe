"""AIICO ARK adapter: monthly-file discovery + AIICO rules (aiico_common)."""
from __future__ import annotations

from cre_cleaner.adapters.aiico_common import AiicoRulesMixin
from cre_cleaner.adapters.monthly_files import MonthlyPremiumAdapter


class AiicoArkAdapter(AiicoRulesMixin, MonthlyPremiumAdapter):
    cedant = "AIICO"
    broker = "ARK"
    verified = True
    # IMPL-20260929-02: tab/table type from content (verified on all AIICO ARK
    # raw tabs); filename still gives month/period and the controlling files.
    content_sheet_typing = True
