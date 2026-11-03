"""NEM adapters (SCIB, AON) and their shared premium / claims rules.

Shared premium layouts
----------------------
Shared NEM premium layouts used by SCIB and AON returns.

NEM SCIB
--------
NEM via SCIB — first-pass, unverified.

One combined Premium/Loss/Outstanding workbook per quarter (same pattern as
Custodian). Dollar / foreign treaty folders under AON are separate currency
inputs picked up by filename/folder currency detection.
"""
from __future__ import annotations

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.core.map_columns import PremiumLayoutRules, is_share_header
from src.domain.cre_cleaner.core.normalize import normalize_header
from src.domain.cre_cleaner.models import ExceptionRecord


class NemPremiumLayoutRules(PremiumLayoutRules):
    def _paired_bands(self, norms):
        bands = [(self.band_kind(n), n, i) for i, n in enumerate(norms)
                 if self.band_kind(n)]
        if len(bands) < 2 or any(i + 1 >= len(norms) or norms[i + 1] for _, _, i in bands):
            return []
        return bands

    def named_bands(self, norms):
        bands = self._paired_bands(norms)
        if not bands:
            return None
        result = {k: (None, None, None) for k in ("ret", "sur", "fac")}
        for kind, _label, i in bands:
            if result[kind] == (None, None, None):
                result[kind] = (i, None, i + 1)
        return result

    def extra_named_layers(self, norms, cm):
        for kind, label, i in self._paired_bands(norms):
            if kind == "sur" and i != cm.sur_ppn:
                cm.extra_treaty_blocks.append((label, i, None, i + 1))

    def block_for_ppn(self, pi, norms, si_idxs, prem_idxs):
        # A two-column PPN/premium band has no SI. Do not borrow the
        # adjacent gross SI/premium pair to make a three-column block.
        label = norms[pi].replace("PPN", "").replace("%", "").strip()
        if label and self.band_kind(label):
            own_prem = next((i for i in prem_idxs if abs(i - pi) <= 3
                             and norms[i].startswith(label + " ")), None)
            own_si = next((i for i in si_idxs if abs(i - pi) <= 3
                           and norms[i].startswith(label + " ")), None)
            if own_prem is not None or own_si is not None:
                return pi, own_si, own_prem
        return super().block_for_ppn(pi, norms, si_idxs, prem_idxs)


_NEM_BAND_WORDS = ("RETENTION", "TREATY", "SURPLUS", "QUOTA", "FAC")


class NemClaimsMixin:
    """Claims band layouts shared by NEM SCIB and AON returns.

    - 'RETENTION | SURPLUS TREATY | FACULTATIVE' above '% | AMOUNT' pairs:
      each bare % is the proportion of the AMOUNT after it.
    - 2020 outstanding tabs: a band name over two columns with no
      sub-headers ('NET RETENTION' | '' ): the named column is the %, the
      unlabelled one after it is the amount.

    Adapters with their own claims rules put them in ``nem_claims_layers``;
    these band rules run after it.
    """

    def map_claims_columns(self, header, aliases, sheet_type=""):
        cm = super().map_claims_columns(header, aliases, sheet_type)
        cm = self.nem_claims_layers(cm, header)
        return nem_claims_band_pairs(cm, header)

    def nem_claims_layers(self, cm, header):
        return cm


def nem_claims_band_pairs(cm, header):
    from src.domain.cre_cleaner.core.map_columns import is_share_header

    norms = [normalize_header(h) for h in header]
    # 2025 outstanding tabs name the FAC band 'FAC OUTWARD' (PPN | AMOUNT).
    if cm.get("amount_fac") is None and "FAC OUTWARD AMOUNT" in norms:
        cm.mapping["amount_fac"] = norms.index("FAC OUTWARD AMOUNT")
        if cm.get("ppn_fac") is None and "FAC OUTWARD PPN" in norms:
            cm.mapping["ppn_fac"] = norms.index("FAC OUTWARD PPN")
    taken = set(cm.mapping.values())

    def unlabelled_after(i):
        return i + 1 < len(norms) and not norms[i + 1] and i + 1 not in taken

    def band_name(i):
        return "AMOUNT" not in norms[i] and any(w in norms[i] for w in _NEM_BAND_WORDS)

    for kind in ("ret", "treaty", "fac"):
        i = cm.get("amount_" + kind)
        if i is None or cm.get("ppn_" + kind) is not None:
            continue
        if band_name(i) and unlabelled_after(i):
            cm.mapping["ppn_" + kind], cm.mapping["amount_" + kind] = i, i + 1
            taken.add(i + 1)
        elif i > 0 and i - 1 not in taken and is_share_header(norms[i - 1]):
            cm.mapping["ppn_" + kind] = i - 1
            taken.add(i - 1)
    shifted = []
    for i, label in getattr(cm, "extra_amount_columns", []):
        if band_name(i) and unlabelled_after(i):
            taken.add(i + 1)
            i += 1
        shifted.append((i, label))
    cm.extra_amount_columns = shifted
    return cm


class NemPremiumMixin:
    premium_layout_rules = NemPremiumLayoutRules()
    recover_premium_from_content = True
    premium_alias_extra = {
        "sum_insured": ["OUR SHARE SI"],
        "gross_premium": ["OUR SHARE OF G PREM"],
        "class": ["POLICY CLASS", "RISKTYPE"],
    }

    def map_premium_columns(self, header, group_row, **kwargs):
        cm = super().map_premium_columns(header, group_row, **kwargs)
        cm.combine_treaty_layers = True
        norms = [normalize_header(h) for h in header]
        if not any(self.premium_layout_rules.is_ppn_header(h) for h in norms) \
                and self.premium_layout_rules._paired_bands(norms) and kwargs.get("exceptions") is not None:
            kwargs["exceptions"].append(ExceptionRecord(
                "INFO", "allocation_subheaders_inferred", kwargs["path"].name,
                kwargs.get("sheet", ""), 0,
                "Missing allocation subheaders: bounded NEM retention/treaty/facultative "
                "pairs read as PPN then premium; no sum insured copied into these bands",
            ))
        return cm


# Paid tabs: 'AMOUNT PAID' is the gross claim unless a 'TOTAL CLAIMS PID' /
# 'TOTAL CLAIMS + ADJUSTER'S FEE PAID' column (the split basis) exists.
# Older AON returns (2018–2020) have only 'GROSS AMOUNT' before the bands.
NEM_CLAIMS_TOTAL_FALLBACK = {
    "paid": ("AMOUNT PAID", "GROSS AMOUNT"),
    "outstanding": ("OUTSTANDING AMOUNT", "GROSS AMOUNT"),
}


class NemScibAdapter(NemClaimsMixin, NemPremiumMixin, QuarterlyWorkbookAdapter):
    cedant = "NEM"
    broker = "SCIB"
    combine_treaty_layers = True
    claims_total_fallback = {
        "paid": ("TOTAL CLAIMS PID", "TOTAL CLAIMS + ADJUSTER'S FEE PAID", "AMOUNT PAID", "CLAIM AMOUNT"),
        "outstanding": ("TOTAL", "OUTSTANDING AMOUNT", "GROSS AMOUNT", "CLAIM AMOUNT"),
    }

    def claims_exclude(self, field, norm):
        # CLAIM PAID is money, even though it partially matches DATE CLAIM PAID.
        return field == "paid_date" and "DATE" not in norm and "DT" not in norm.split()

    def nem_claims_layers(self, cm, header):
        # Named treaty layers / shares; the shared NEM band rules run after.
        treaty_amounts = []
        for i, value in enumerate(header):
            name = normalize_header(value)
            if not name:
                continue
            share = is_share_header(name)
            if "QUOTA" in name or "SURPLUS" in name or name.startswith("TREATY "):
                if share:
                    cm.treaty_ppn_columns.append(i)
                elif "TOTAL" not in name:
                    treaty_amounts.append((i, name))
            if share and "RETENTION" in name:
                cm.mapping["ppn_ret"] = i
            if share and "FACULTATIVE" in name:
                cm.mapping["ppn_fac"] = i
        # An explicit aggregate already includes the component allocations.
        aggregate_i = next((i for i, value in enumerate(header)
                            if normalize_header(value) in {
                                "TOTAL RECOVERY", "TOTAL TREATY", "TOTAL TREATY AMOUNT",
                            }), None)
        if aggregate_i is not None:
            cm.mapping["amount_treaty"] = aggregate_i
            cm.combine_treaty_layers = True
        primary = cm.get("amount_treaty")
        aggregate = primary is not None and "TOTAL" in normalize_header(header[primary])
        if treaty_amounts and not aggregate:
            if primary not in {i for i, _ in treaty_amounts}:
                cm.mapping["amount_treaty"] = treaty_amounts[0][0]
            primary = cm.get("amount_treaty")
            cm.extra_amount_columns = [(i, label) for i, label in treaty_amounts if i != primary]
            cm.combine_treaty_layers = True
        if cm.treaty_ppn_columns:
            cm.mapping["ppn_treaty"] = cm.treaty_ppn_columns[0]
        return cm

    verified = False
    status_note = (
        "NEM SCIB: quarterly combined Premium/Loss workbooks; not checked against Bisola gold"
    )


class NemAonAdapter(NemClaimsMixin, NemPremiumMixin, QuarterlyWorkbookAdapter):
    cedant = "NEM"
    broker = "AON"
    claims_total_fallback = NEM_CLAIMS_TOTAL_FALLBACK
    verified = False
    status_note = "NEM/AON: quarterly (incl. dollar) returns; unverified"
