"""NEM via SCIB — first-pass, unverified.

One combined Premium/Loss/Outstanding workbook per quarter (same pattern as
Custodian). Dollar / foreign treaty folders under AON are separate currency
inputs picked up by filename/folder currency detection.
"""
from __future__ import annotations

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.adapters.nem_common import NemClaimsMixin, NemPremiumMixin, NemPremiumLayoutRules  # noqa: F401
from src.domain.cre_cleaner.core.map_columns import is_share_header
from src.domain.cre_cleaner.core.normalize import normalize_header


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
