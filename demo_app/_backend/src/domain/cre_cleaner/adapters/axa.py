"""AXA Mansard (Direct) adapter — AXA rules only (IMPL-20260929-04).

AXA premium returns carry no PPN triples; bands are labelled columns
(OUR RETENTION SUM INSURED / OUR RETENTION PREMIUM / TREATY PREMIUM …) and
each band's share is a bare 'PCNT' column sitting *after* that band's
premium, so PCNT columns are assigned by position.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.core.map_columns import ColumnMap, PremiumLayoutRules, find_named_column

Band = Tuple[Optional[int], Optional[int], Optional[int]]


class AxaPremiumRules(PremiumLayoutRules):
    RET_PPN = ("OUR RETENTION PCNT", "RETENTION PPN", "PCNT")
    RET_SI = ("OUR RETENTION SUM INSURED", "RETENTION SUM INSURED")
    RET_PREM = ("OUR RETENTION PREMIUM", "RETENTION PREMIUM", "RETENTION")
    SUR_SI = ("TREATY SUM INSURED", "QUOTA SHARE SUM INSURED", "1ST SURPLUS SUM INSURED")
    SUR_PREM = ("TREATY PREMIUM", "QUOTA SHARE PREMIUM", "TREATY FIRST SURPLUS",
                "1ST SURPLUS", "1ST SURP", "TREATY")
    FAC_SI = ("FACULTATIVE SUM INSURED", "FAC SUM INSURED")
    FAC_PREM = ("FACULTATIVE PREMIUM", "FAC PREMIUM", "FACULTATIVE")

    def named_bands(self, norms: List[str]) -> Optional[Dict[str, Band]]:
        f = lambda needles: find_named_column(norms, needles)  # noqa: E731
        ret_ppn, ret_si, ret_prem = f(self.RET_PPN), f(self.RET_SI), f(self.RET_PREM)
        sur_ppn, sur_si, sur_prem = None, f(self.SUR_SI), f(self.SUR_PREM)
        fac_ppn, fac_si, fac_prem = None, f(self.FAC_SI), f(self.FAC_PREM)
        # PCNT columns sit after each band's premium — assign by position.
        pcnts = [i for i, n in enumerate(norms) if n == "PCNT"]
        if ret_prem is not None and sur_prem is not None and len(pcnts) >= 2:
            ret_ppn = next((i for i in pcnts if ret_prem < i < sur_prem), pcnts[0])
            sur_ppn = next((i for i in pcnts if i > sur_prem), pcnts[1])
            if fac_prem is not None:
                fac_ppn = next((i for i in pcnts if i > fac_prem), None)
        elif ret_prem is not None and pcnts:
            ret_ppn = ret_ppn or next((i for i in pcnts if i > ret_prem), None)
        return {"ret": (ret_ppn, ret_si, ret_prem), "sur": (sur_ppn, sur_si, sur_prem),
                "fac": (fac_ppn, fac_si, fac_prem)}

    def extra_named_layers(self, norms: List[str], cm: ColumnMap) -> None:
        # OBLIG TREATY as a further layer when TREATY is already taken.
        oblig_prem = next((i for i, n in enumerate(norms) if "OBLIG" in n and "PREMIUM" in n), None)
        oblig_si = next((i for i, n in enumerate(norms) if "OBLIG" in n and "SUM INSURED" in n), None)
        if oblig_prem is not None:
            cm.extra_treaty_blocks.append(("OBLIG TREATY", None, oblig_si, oblig_prem))


class AxaDirectAdapter(QuarterlyWorkbookAdapter):
    cedant = "AXA"
    broker = "DIRECT"
    verified = False
    status_note = (
        "AXA Mansard: quarterly premium/claims Excel + PDFs (via LlamaParse); "
        "not checked against Bisola gold"
    )
    premium_layout_rules = AxaPremiumRules()
