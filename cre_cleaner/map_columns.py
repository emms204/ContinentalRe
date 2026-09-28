"""Fuzzy / alias map source columns → template fields."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cre_cleaner.normalize import normalize_header


# Canonical target → accepted normalized aliases
PREMIUM_ALIASES: Dict[str, List[str]] = {
    "policy_no": [
        "POLICY NO", "POLICY NO.", "POLICY NUMBER", "POLICYNUMBER", "POL NO",
        "POLICY",
    ],
    "insured": [
        "INSURED", "NAME OF INSURED", "NAME OF THE INSURED", "ASSURED",
        "NAME OF ASSURED",
    ],
    "channel": ["CHANNEL"],
    "sub_channel": ["SUB CHANNEL", "SUBCHANNEL", "SUB CLASS", "SUBCLASS"],
    "uw_year": [
        "UNDERWRITING YEAR", "UW YR", "UW YEAR", "U YEAR", "U/YEAR", "UY",
        "UW",
    ],
    "period": [
        "PERIOD OF INSURANCE", "INSURANCE PERIOD", "PERIOD OF COVER",
        "PERIOD", "COVER PERIOD",
    ],
    "period_from": ["FROM", "COVER FROM", "PERIOD FROM", "INCEPTION"],
    "period_to": ["TO", "COVER TO", "PERIOD TO", "EXPIRY"],
    "sum_insured": [
        "TOTAL SUM INSURED", "SUM INSURED", "TSI", "SI",
    ],
    "mpl": ["MPL", "MPL %", "MPL PCT"],
    "gross_premium": [
        "GROSS PREMIUM", "PREMIUM", "GP", "GROSS PREM",
    ],
    "debit_note": ["DEBIT NOTE", "DEBITNOTE", "DN", "CREDIT NOTE"],
    "currency": ["CURRENCY", "CCY"],
}

CLAIMS_ALIASES: Dict[str, List[str]] = {
    "insured": [
        "INSURED", "NAME OF INSURED", "NAME OF INSURED CLAIMANT",
        "INSURED CLAIMANT", "CLAIMANT",
    ],
    "class": ["CLASS", "SUB CLASS", "SUBCLASS"],
    "policy_no": ["POLICY NO", "POLICY NO.", "POLICY NUMBER", "POLICY"],
    "claim_no": ["CLAIM NO", "CLAIM NO.", "CLAIM NUMBER", "CLAIMNUMBER"],
    "date_of_loss": [
        "DATE OF LOSS", "DATE OF LOSS DAY MTH YEAR", "LOSS DATE",
        "DATE OF", "DOL",
    ],
    "uw_yr": ["UW YR", "UW YEAR", "U YEAR", "U/YEAR", "UNDERWRITING YEAR", "UY"],
    "period": ["PERIOD OF COVER", "PERIOD OF INSURANCE", "PERIOD"],
    "period_from": ["FROM", "COVER FROM", "PERIOD FROM"],
    "period_to": ["TO", "COVER TO", "PERIOD TO"],
    "total_claims": [
        "TOTAL CLAIMS", "TOTAL CLAIMS CLAIMS", "TOTAL CLAIMS PAID",
        "TOTAL OST RESERVE", "TOTAL OST", "OST RESERVE",
        "TOTAL OUTSTANDING", "TOTAL CLAIM",
    ],
    "amount_ret": [
        "AIICO S NET LIABILITY", "AIICOS NET LIABILITY", "OWN SHARE",
        "OWN SHARE.", "NET LIABILITY", "RETENTION AMOUNT", "OWN RETENTION",
        "AMOUNT RET",
    ],
    "amount_treaty": [
        "1ST SURP", "1ST SURPLUS", "2ND SURP", "2ND SURPLUS",
        "SURPLUS", "TREATY", "TREATY SHARE", "AMOUNT TREATY",
    ],
    "amount_fac": [
        "FAC OBLIG", "FAC", "FACULTATIVE", "FAC AMOUNT", "AMOUNT FAC",
    ],
    "details": [
        "DETAILS OF LOSS", "DESCRIPTION OF LOSS", "DECSRIPTION OF LOSS",
        "DESCRIPTION OF LOSS", "LOSS DESCRIPTION", "PARTICULARS",
    ],
    "month": ["MONTH"],
    "paid_date": ["PAYMENT DATE", "DATE PAID", "PAID DATE", "DATE OF PAYMENT"],
    "currency": ["CURRENCY", "CCY"],
}


def merged_aliases(
    base: Dict[str, List[str]], extra: Optional[Dict[str, List[str]]],
) -> Dict[str, List[str]]:
    """Default aliases plus cedant-specific ones; field order stays the default
    order (earlier fields claim a column first)."""
    if not extra:
        return base
    out = {k: list(extra.get(k, [])) + list(v) for k, v in base.items()}
    for k, v in extra.items():
        out.setdefault(k, list(v))
    return out


@dataclass
class ColumnMap:
    """Maps logical field → 0-based source column index."""
    mapping: Dict[str, int] = field(default_factory=dict)
    # For premium allocation blocks detected via group headers
    ret_ppn: Optional[int] = None
    ret_si: Optional[int] = None
    ret_prem: Optional[int] = None
    sur_ppn: Optional[int] = None
    sur_si: Optional[int] = None
    sur_prem: Optional[int] = None
    fac_ppn: Optional[int] = None
    fac_si: Optional[int] = None
    fac_prem: Optional[int] = None
    # Group label of the treaty block written to the TREATY band (e.g. "1SURP").
    sur_label: str = ""
    # Further treaty layers: (label, ppn, si, prem) column indices.
    extra_treaty_blocks: List[Tuple[str, Optional[int], Optional[int], Optional[int]]] = field(
        default_factory=list
    )
    # Second RET/FAC blocks that have no place in the upload schema.
    ignored_blocks: List[str] = field(default_factory=list)
    unmapped_headers: List[str] = field(default_factory=list)

    def get(self, field: str) -> Optional[int]:
        return self.mapping.get(field)


def _alias_match(norm: str, aliases: List[str]) -> bool:
    for a in aliases:
        an = normalize_header(a)
        if norm == an:
            return True
        # allow contained match for long headers
        if an and (an == norm or norm.endswith(" " + an) or an.endswith(" " + norm)):
            return True
        if an in norm and len(an) >= 4:
            return True
    return False


def map_simple_columns(
    headers: Sequence[Any],
    alias_table: Dict[str, List[str]],
) -> ColumnMap:
    cm = ColumnMap()
    norms = [normalize_header(h) if h is not None else "" for h in headers]
    used = set()
    for field, aliases in alias_table.items():
        best = None
        for i, n in enumerate(norms):
            if i in used or not n:
                continue
            if _alias_match(n, aliases):
                # Prefer exact / shorter over greedy
                best = i
                break
        if best is not None:
            cm.mapping[field] = best
            used.add(best)
    cm.unmapped_headers = [norms[i] for i in range(len(norms)) if i not in used and norms[i]]
    return cm


def extra_alias_columns(
    headers: Sequence[Any],
    aliases: List[str],
    used: Sequence[Optional[int]],
) -> List[Tuple[int, str]]:
    """Further amount columns matching ``aliases`` that the first-match mapping
    did not take (e.g. a 2ND SURPLUS column next to 1ST SURPLUS). Share / %
    columns are not amounts and are excluded."""
    taken = {i for i in used if i is not None}
    out = []
    for i, h in enumerate(headers):
        n = normalize_header(h) if h is not None else ""
        if not n or i in taken:
            continue
        if "%" in n or any(t in n.split() for t in ("PPN", "PROPORTION", "RATE", "SHARE")):
            continue
        if _alias_match(n, aliases):
            out.append((i, n))
    return out


def detect_premium_allocation_blocks(
    header_row: Sequence[Any],
    group_row: Optional[Sequence[Any]] = None,
    aliases: Optional[Dict[str, List[str]]] = None,
) -> ColumnMap:
    """
    AIICO ARK premium layout:
      group row: OWN RETENTION | 1SURP / 2SURP / QUOTA
      header: INSURED, POLICY NO, DEBIT NOTE, PERIOD, [UW YR], SUM INSURED, PREMIUM,
              PPN, SUM INSURED, Premium, PPN, SUM INSURED, Premium
    Gross SI/Premium are the first SUM INSURED / PREMIUM after period/UW.
    Retention and Surplus triples follow.
    """
    cm = map_simple_columns(header_row, aliases or PREMIUM_ALIASES)
    norms = [normalize_header(h) if h is not None else "" for h in header_row]
    groups = []
    if group_row:
        groups = [normalize_header(h) if h is not None else "" for h in group_row]

    # Locate PPN columns
    ppn_idxs = [i for i, n in enumerate(norms) if n in {"PPN", "PPN %", "PROPORTION", "PROPORTION %"}]
    # Or blank-ish "PPN " already normalized to PPN

    # Find gross premium: first PREMIUM that is NOT immediately after a PPN block's SI
    # Strategy: find columns labeled SUM INSURED and PREMIUM/Premium in order
    si_idxs = [i for i, n in enumerate(norms) if n in {"SUM INSURED", "TOTAL SUM INSURED"}]
    prem_idxs = [i for i, n in enumerate(norms) if n in {"PREMIUM", "GROSS PREMIUM"}]

    # Gross = first SI + first PREMIUM that appear before first allocation PPN
    first_ppn = min(ppn_idxs) if ppn_idxs else len(norms)
    gross_si = next((i for i in si_idxs if i < first_ppn), None)
    gross_prem = next((i for i in prem_idxs if i < first_ppn), None)
    if gross_si is not None:
        cm.mapping["sum_insured"] = gross_si
    if gross_prem is not None:
        cm.mapping["gross_premium"] = gross_prem

    # Allocation triples: each PPN followed by SI and Premium
    blocks = []
    for pi in ppn_idxs:
        # next SI and Premium after pi
        si = next((i for i in si_idxs if i > pi), None)
        pr = next((i for i in prem_idxs if i > pi), None)
        if si is not None and pr is not None and si < pi + 4 and pr < pi + 4:
            blocks.append((pi, si, pr))

    # Classify blocks using group headers above PPN columns
    def group_for(col: int) -> str:
        if not groups:
            return ""
        # scan left for nearest non-empty group label
        label = ""
        for j in range(col, -1, -1):
            if j < len(groups) and groups[j]:
                label = groups[j]
                break
        return label

    ret_set = False
    sur_set = False
    fac_set = False
    used: set = set()
    for idx, (pi, si, pr) in enumerate(blocks):
        g = group_for(pi)
        is_ret = any(x in g for x in ("OWN RETENTION", "RETENTION", "RETAINED"))
        is_fac = any(x in g for x in ("FAC", "FACULTATIVE"))
        is_sur = any(x in g for x in ("SURP", "SURPLUS", "TREATY", "QUOTA", "QS"))
        # Only assign from an explicit group label here. Unlabelled blocks are
        # handled below (first → retention, second → treaty). The old
        # `(is_sur or not sur_set)` branch put the first unlabelled block into
        # TREATY and the second into RETENTION — the opposite of the layout.
        if is_ret and not ret_set:
            cm.ret_ppn, cm.ret_si, cm.ret_prem = pi, si, pr
            ret_set = True
            used.add(idx)
        elif is_fac and not fac_set:
            cm.fac_ppn, cm.fac_si, cm.fac_prem = pi, si, pr
            fac_set = True
            used.add(idx)
        elif is_sur and not sur_set:
            cm.sur_ppn, cm.sur_si, cm.sur_prem = pi, si, pr
            cm.sur_label = g or "TREATY"
            sur_set = True
            used.add(idx)
        elif is_sur and sur_set:
            cm.extra_treaty_blocks.append((g or f"TREATY BLOCK {idx + 1}", pi, si, pr))
            used.add(idx)
        elif is_ret or is_fac:
            cm.ignored_blocks.append(f"{g} (cols {pi + 1}-{pr + 1})")
            used.add(idx)
        # else: unlabelled — fall through to positional assignment below

    unlabelled = [blocks[i] for i in range(len(blocks)) if i not in used]
    # No / remaining group labels: first block = retention, second = treaty.
    # A single unlabelled block alone is treated as treaty (retention stays
    # blank) so treaty-only sheets do not land in the RETENTION band.
    if unlabelled and not ret_set and not sur_set:
        if len(unlabelled) == 1:
            pi, si, pr = unlabelled[0]
            cm.sur_ppn, cm.sur_si, cm.sur_prem = pi, si, pr
            cm.sur_label = "TREATY"
        else:
            pi, si, pr = unlabelled[0]
            cm.ret_ppn, cm.ret_si, cm.ret_prem = pi, si, pr
            pi, si, pr = unlabelled[1]
            cm.sur_ppn, cm.sur_si, cm.sur_prem = pi, si, pr
            cm.sur_label = "TREATY"
            for i, (pi, si, pr) in enumerate(unlabelled[2:], start=3):
                cm.extra_treaty_blocks.append((f"TREATY BLOCK {i}", pi, si, pr))
    elif unlabelled:
        # Some blocks already labelled; assign leftovers in order.
        for i, (pi, si, pr) in enumerate(unlabelled):
            if not ret_set:
                cm.ret_ppn, cm.ret_si, cm.ret_prem = pi, si, pr
                ret_set = True
            elif not sur_set:
                cm.sur_ppn, cm.sur_si, cm.sur_prem = pi, si, pr
                cm.sur_label = "TREATY"
                sur_set = True
            else:
                cm.extra_treaty_blocks.append((f"TREATY BLOCK {i + 1}", pi, si, pr))

    return cm


def cell(row: Sequence[Any], idx: Optional[int]) -> Any:
    if idx is None:
        return None
    if idx < 0 or idx >= len(row):
        return None
    return row[idx]
