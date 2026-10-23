"""SUNU UAIB's explicit net/treaty/FAC source amounts and trailing shares."""

from src.domain.cre_cleaner.adapters.source_layouts import SourceLayoutAdapter
from src.domain.cre_cleaner.core.normalize import normalize_header
from src.domain.cre_cleaner.core.map_columns import map_simple_columns


class SunuUaibAdapter(SourceLayoutAdapter):
    cedant = "SUNU"
    broker = "UAIB"
    class_label_extra = {
        "ENG": "Engineering",
        "CARGO": "Marine Cargo",
        "HULL": "Marine Hull",
        "GEN ACC": "General Accident",
        "GEN": "General Accident",
        "GEN ACCD": "General Accident",
        "GACCIDENT": "General Accident",
    }
    class_label_exact_only = {"GEN", "ENG"}
    claims_alias_extra = {
        "period_from": ["PERIOD BEGIN"],
        "period_to": ["PERIOD END"],
        "amount_ret": ["NET AMOUNT"],
        "total_claims": ["AMOUNT PAID", "OUTSTANDING AMOUNT"],
    }
    claims_total_fallback = {
        "paid": ("AMOUNT PAID",),
        "outstanding": ("OUTSTANDING AMOUNT",),
    }

    def map_premium_columns(self, header, group_row, **kwargs):
        norms = [normalize_header(v) for v in header]
        bands = [
            ("ret", {"NET SUM INSURED"}, {"NET PREMIUM"}),
            ("sur", {"TREATY SUM INSURED"}, {"TREATY PREMIUM"}),
            ("fac", {"FAC OUT SUM INSURED", "FAC OUT SUMINSURED"}, {"FAC OUT PREMIUM"}),
        ]
        starts = [i for i, h in enumerate(norms) if any(h in si for _, si, _ in bands)]
        if len(starts) < 2:
            return super().map_premium_columns(header, group_row, **kwargs)
        cm = map_simple_columns(
            header, self.premium_aliases(), exclude=self.premium_exclude
        )
        for kind, si_names, prem_names in bands:
            si = next((i for i, h in enumerate(norms) if h in si_names), None)
            prem = next((i for i, h in enumerate(norms) if h in prem_names), None)
            end = min([i for i in starts if si is not None and i > si] + [len(norms)])
            ppn = next(
                (
                    i
                    for i in range((prem + 1) if prem is not None else end, end)
                    if norms[i] in {"%", "PPN"}
                ),
                None,
            )
            setattr(cm, kind + "_si", si)
            setattr(cm, kind + "_prem", prem)
            setattr(cm, kind + "_ppn", ppn)
        cm.sur_label = "TREATY"
        return cm
