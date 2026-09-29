"""Fuzzy / alias map source columns → template fields."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cre_cleaner.core.normalize import normalize_header


# Canonical target → accepted normalized aliases
PREMIUM_ALIASES: Dict[str, List[str]] = {
    "policy_no": [
        "POLICY NUMBER", "POLICY NO", "POLICY NO.", "POLICYNUMBER", "POL NO",
        "POLICY KEY", "POLICY",
    ],
    "insured": [
        "NAME OF INSURED", "NAME OF THE INSURED", "INSURED NAME",
        "NAME OF ASSURED", "CUSTOMERS NAME", "INSURED", "ASSURED",
    ],
    "class": [
        "RISKS TYPE", "RISK TYPE", "POLICY CLASS", "POL CLASS", "COVER TYPE",
        "CLASS",
    ],
    "channel": ["CHANNEL"],
    "sub_channel": ["SUB CHANNEL", "SUBCHANNEL", "SUB CLASS", "SUBCLASS"],
    "uw_year": [
        "UNDERWRITING YEAR", "UW YEAR", "UW YR", "UDW YEAR", "U YEAR", "U/YEAR",
        "UY", "UW",
    ],
    "period_from": [
        "COVER START", "PERIOD FROM", "START DATE", "COVER FROM", "FROM",
        "INCEPTION", "EFFECTIVE DATE",
    ],
    "period_to": [
        "COVER END", "PERIOD TO", "END DATE", "COVER TO", "TO", "EXPIRY",
        "EXPIRY DATE",
    ],
    "period": [
        "PERIOD OF INSURANCE", "INSURANCE PERIOD", "PERIOD OF COVER",
        "PERIOD", "COVER PERIOD",
    ],
    "sum_insured": [
        "GROSS SUM INSURED", "TOTAL SUM INSURED", "OUR SHARE SI",
        "OUR SHARE SUM INSURED", "SUM INSURED", "TSI", "SI",
    ],
    "mpl": ["MPL", "MPL %", "MPL PCT"],
    "gross_premium": [
        "OUR SHARE OF G PREM", "OUR SHARE GROSS PREMIUM", "GROSS PREMIUM",
        "GROSS PREM", "PREMIUM", "GP",
    ],
    "debit_note": ["DEBIT NOTE", "DEBITNOTE", "DN", "CREDIT NOTE"],
    "currency": ["CURRENCY", "CCY"],
}

CLAIMS_ALIASES: Dict[str, List[str]] = {
    "insured": [
        "NAME OF INSURED CLAIMANT", "NAME OF INSURED", "INSURED NAME",
        "INSURED CLAIMANT", "INSURED", "CLAIMANT",
    ],
    "class": ["CLASS", "SUB CLASS", "SUBCLASS", "POL CLASS", "POLICY CLASS"],
    "policy_no": ["POLICY NO", "POLICY NO.", "POLICY NUMBER", "POLICY"],
    "claim_no": ["CLAIM NO", "CLAIM NO.", "CLAIM NUMBER", "CLAIMNUMBER"],
    "date_of_loss": [
        "DATE OF LOSS INCIDENT", "DATE OF LOSS", "DATE OF LOSS DAY MTH YEAR",
        "LOSS DATE", "DATE OF", "DOL",
    ],
    "uw_yr": ["UW YR", "UW YEAR", "U YEAR", "U/YEAR", "UNDERWRITING YEAR", "UY"],
    "period_from": [
        "PERIOD OF COVER FROM", "COVER FROM", "PERIOD FROM", "FROM", "START DATE",
    ],
    "period_to": [
        "PERIOD OF COVER TO", "COVER TO", "PERIOD TO", "TO", "END DATE",
    ],
    "period": ["PERIOD OF COVER", "PERIOD OF INSURANCE", "PERIOD"],
    "total_claims": [
        "TOTAL CLAIMS PAID", "TOTAL AMOUNT PAID", "TOTAL CLAIMS CLAIMS",
        "TOTAL CLAIMS", "GROSS LOSS RESERVE", "TOTAL RESERVE AMOUNT",
        "TOTAL OST RESERVE", "TOTAL OST", "OST RESERVE", "TOTAL OUTSTANDING",
        "TOTAL CLAIM", "TOTAL CLAIM PAID", "LOSS RESERVE",
    ],
    "amount_ret": [
        "AIICO S NET LIABILITY", "AIICOS NET LIABILITY", "OWN SHARE",
        "OWN SHARE.", "NET LIABILITY", "RETENTION AMOUNT", "OWN RETENTION",
        "NET RETENTION", "NET RETENTIONS", "AMOUNT RET",
    ],
    "amount_treaty": [
        "TREATY RECOVERY", "TOTAL RECOVERY", "TREATY AMOUNT", "TTY AMOUNT",
        "1ST SURP", "1ST SURPLUS", "2ND SURP", "2ND SURPLUS",
        "TREATY FIRST SURPLUS", "TREATY QUOTA SHARE", "SURPLUS",
        "TREATY SHARE", "AMOUNT TREATY", "TREATY",
    ],
    "amount_fac": [
        "FAC OBLIG", "FAC AMOUNT", "AMOUNT FAC", "FACULTATIVE", "FAC",
    ],
    "details": [
        "DETAILS OF LOSS", "DESCRIPTION OF LOSS", "DECSRIPTION OF LOSS",
        "DESCRIPTION OF LOSS", "LOSS DESCRIPTION", "PARTICULARS",
        "DETAILS OF INCIDENT",
    ],
    "month": ["MONTH"],
    "paid_date": [
        "PAYMENT DATE", "DATE PAID", "PAID DATE", "DATE OF PAYMENT",
        "DATE PAID TO BE PAID", "DATE CLAIM PAID",
    ],
    "currency": ["CURRENCY", "CCY"],
}

# Amount-band fields: never map a % / PPN / share-only header into these.
_AMOUNT_FIELDS = {
    "amount_ret", "amount_treaty", "amount_fac", "total_claims",
    "sum_insured", "gross_premium",
}
_SHARE_PREFIX_REJECT = {"SUB", "GROSS", "NET", "TOTAL", "OUR", "OWN", "SBU"}


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


def is_share_header(norm: str) -> bool:
    """True for percentage / proportion headers (not money amounts)."""
    if not norm:
        return False
    if norm in {"%", "PPN", "PPN %", "PROPORTION", "PROPORTION %", "RATE", "PCNT", "PCT"}:
        return True
    if norm.endswith("%") or " PPN" in f" {norm}" or norm.endswith(" PPN"):
        return True
    toks = set(norm.split())
    if toks & {"PPN", "PROPORTION", "RATE", "PCNT", "PCT"} and not (
        toks & {"PREMIUM", "AMOUNT", "SUM", "INSURED", "SI", "PREM"}
    ):
        return True
    return False


def _alias_match(norm: str, aliases: List[str]) -> bool:
    return _alias_score(norm, aliases) > 0


def _alias_score(norm: str, aliases: List[str]) -> int:
    """Higher is better. Exact match beats contained match."""
    best = 0
    for a in aliases:
        an = normalize_header(a)
        if not an or not norm:
            continue
        if norm == an:
            best = max(best, 1000 + len(an))
            continue
        # Header more specific than alias: "POLICY NUMBER" vs "POLICY"
        if len(an) >= 4 and (
            norm == an
            or norm.startswith(an + " ")
            or norm.endswith(" " + an)
            or f" {an} " in f" {norm} "
        ):
            best = max(best, 100 + len(an))
            continue
        # Alias more specific than header: "NAME OF INSURED" vs "INSURED"
        if an.endswith(" " + norm) and len(norm) >= 4:
            prefix = an[: -(len(norm) + 1)]
            # Reject SUB CLASS → CLASS, OWN RETENTION → RETENTION (bare), etc.
            first = prefix.split()[0] if prefix else ""
            if first in _SHARE_PREFIX_REJECT:
                continue
            best = max(best, 50 + len(norm))
            continue
        if an in norm and len(an) >= 4:
            best = max(best, 10 + len(an))
    return best


def map_simple_columns(
    headers: Sequence[Any],
    alias_table: Dict[str, List[str]],
) -> ColumnMap:
    cm = ColumnMap()
    norms = [normalize_header(h) if h is not None else "" for h in headers]
    used = set()
    for field, aliases in alias_table.items():
        best_i, best_score = None, 0
        for i, n in enumerate(norms):
            if i in used or not n:
                continue
            if field in _AMOUNT_FIELDS and is_share_header(n):
                continue
            score = _alias_score(n, aliases)
            if score > best_score:
                best_score = score
                best_i = i
        if best_i is not None:
            cm.mapping[field] = best_i
            used.add(best_i)
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
        if is_share_header(n):
            continue
        if _alias_match(n, aliases):
            out.append((i, n))
    return out


def merge_group_subheaders(
    group_row: Sequence[Any],
    sub_row: Sequence[Any],
) -> List[str]:
    """Combine a spanning band row (RETENTION | TREATY) with %/AMOUNT/SI/Prem subheaders.

    LASACO OS::
      row1: … GROSS LOSS RESERVE | RETENTION | | TREATY |
      row2: …                    | %         | AMOUNT | % | AMOUNT
    → RETENTION PPN, RETENTION AMOUNT, TREATY PPN, TREATY AMOUNT

    UNITRUST premium::
      row1: … OUR SHARE … | RETENTION | | | TREATY |
      row2: …             | SUM INSURED | GROSS PREMIUM | % | SUM INSURED | …
    → RETENTION SUM INSURED, RETENTION GROSS PREMIUM, RETENTION PPN, …
    """
    width = max(len(group_row), len(sub_row))
    groups: List[str] = []
    last = ""
    for i in range(width):
        g = normalize_header(group_row[i]) if i < len(group_row) and group_row[i] is not None else ""
        if g:
            last = g
        groups.append(last if not g else g)
    out: List[str] = []
    for i in range(width):
        sub = normalize_header(sub_row[i]) if i < len(sub_row) and sub_row[i] is not None else ""
        g = groups[i] if i < len(groups) else ""
        top = normalize_header(group_row[i]) if i < len(group_row) and group_row[i] is not None else ""
        if sub in {"%", "PPN", "PPN %"} or (sub.endswith("%") and "AMOUNT" not in sub and "PREMIUM" not in sub):
            out.append(f"{g} PPN".strip() if g else sub)
        elif sub in {"AMOUNT", "AMT"} or sub.endswith(" AMOUNT"):
            out.append(f"{g} AMOUNT".strip() if g else sub)
        elif sub in {"PREMIUM", "GROSS PREMIUM", "SUM INSURED", "SI"} and g:
            out.append(f"{g} {sub}".strip())
        elif top:
            out.append(top)
        elif sub:
            out.append(sub)
        elif g and any(
            normalize_header(sub_row[j]) if j < len(sub_row) and sub_row[j] is not None else ""
            for j in range(i, min(i + 1, width))
        ):
            out.append(g)
        else:
            # Do not propagate a band label into trailing empty columns.
            out.append("")
    return out


def _looks_like_allocation_subrow(sub: Sequence[Any]) -> bool:
    """True when the row under a header is % / AMOUNT / SI / Premium band labels."""
    markers = 0
    for c in sub:
        n = normalize_header(c) if c is not None else ""
        if n in {"%", "PPN", "AMOUNT", "AMT", "SUM INSURED", "SI", "PREMIUM", "GROSS PREMIUM"}:
            markers += 1
        elif n.endswith("%") and "PREMIUM" not in n:
            markers += 1
    return markers >= 2


def _is_ppn_header(norm: str) -> bool:
    if not norm:
        return False
    if norm in {"PPN", "PPN %", "PROPORTION", "PROPORTION %", "%"}:
        return True
    if re.fullmatch(r"PPN\d*", norm):
        return True
    if norm.endswith(" PPN") or " PPN " in f" {norm} " or norm.endswith(" PPN%"):
        return True
    if norm in {"RTN%", "RTNTN PPN", "TREATY%", "TREATY %", "TREATY PPN", "TREATY PPN%"}:
        return True
    if norm.endswith("%") and any(
        x in norm for x in ("RTN", "RET", "TREATY", "FAC", "QUOTA", "SURP")
    ):
        return True
    return False


def _looks_like_si(norm: str) -> bool:
    return norm in {
        "SUM INSURED", "TOTAL SUM INSURED", "RTNTN SUM INSURED",
        "QUOTA SHARE SUM INSURED", "TREATY SUM INSURED", "OUR RETENTION SUM INSURED",
        "OBLIG TREATY SUM INSURED", "FACULTATIVE SUM INSURED", "SI",
    } or (norm.endswith(" SUM INSURED") or norm.endswith(" SI"))


def _looks_like_prem(norm: str) -> bool:
    if not norm or is_share_header(norm):
        return False
    if norm in {
        "PREMIUM", "GROSS PREMIUM", "RTNTN PREMIUM", "QUOTA SHARE PREMIUM",
        "TREATY PREMIUM", "OUR RETENTION PREMIUM", "OBLIG TREATY PREMIUM",
        "FACULTATIVE PREMIUM", "PREMIUM1", "RETENTION GROSS PREMIUM",
        "TREATY GROSS PREMIUM", "RETENTION PREMIUM",
    }:
        return True
    return (
        norm.endswith(" PREMIUM")
        or norm.endswith(" PREM")
        or norm.endswith(" GROSS PREMIUM")
    )


def _named_band_columns(norms: List[str]) -> Dict[str, Tuple[Optional[int], Optional[int], Optional[int]]]:
    """AXA / UNITRUST style: OUR RETENTION PREMIUM, TREATY PREMIUM, NET RETENTION."""
    def find(*needles: str) -> Optional[int]:
        for i, n in enumerate(norms):
            if n in needles:
                return i
        for i, n in enumerate(norms):
            for needle in needles:
                if n == needle or n.endswith(" " + needle) or needle in n:
                    if is_share_header(n):
                        continue
                    return i
        return None

    ret_ppn = find("OUR RETENTION PCNT", "RTNTN PPN", "RTN%", "RETENTION PPN", "PCNT")
    # First PCNT after retention SI/prem is often the retention share (AXA).
    ret_si = find(
        "OUR RETENTION SUM INSURED", "RTNTN SUM INSURED", "RETENTION SUM INSURED",
    )
    ret_prem = find(
        "OUR RETENTION PREMIUM", "RTNTN PREMIUM", "RETENTION PREMIUM",
        "NET RETENTION", "NET RETENTIONS", "RETENTION",
    )
    sur_ppn = None
    sur_si = find(
        "TREATY SUM INSURED", "QUOTA SHARE SUM INSURED", "1ST SURPLUS SUM INSURED",
    )
    sur_prem = find(
        "TREATY PREMIUM", "QUOTA SHARE PREMIUM", "TREATY QUOTA SHARE",
        "TREATY FIRST SURPLUS", "1ST SURPLUS", "1ST SURP", "TREATY",
    )
    fac_ppn = None
    fac_si = find("FACULTATIVE SUM INSURED", "FAC SUM INSURED")
    fac_prem = find("FACULTATIVE PREMIUM", "FAC PREMIUM", "FACULTATIVE")

    # AXA: PCNT columns sit after each band's premium — assign by position.
    pcnts = [i for i, n in enumerate(norms) if n == "PCNT"]
    if ret_prem is not None and sur_prem is not None and len(pcnts) >= 2:
        ret_ppn = next((i for i in pcnts if ret_prem < i < sur_prem), pcnts[0])
        sur_ppn = next((i for i in pcnts if i > sur_prem), pcnts[1] if len(pcnts) > 1 else None)
        if fac_prem is not None:
            fac_ppn = next((i for i in pcnts if i > fac_prem), None)
    elif ret_prem is not None and pcnts:
        ret_ppn = ret_ppn or next((i for i in pcnts if i > ret_prem), None)

    return {
        "ret": (ret_ppn, ret_si, ret_prem),
        "sur": (sur_ppn, sur_si, sur_prem),
        "fac": (fac_ppn, fac_si, fac_prem),
    }


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
    Also handles RTNTN PPN / QUOTA SHARE PPN labelled columns and AXA-style
    OUR RETENTION PREMIUM / TREATY PREMIUM named bands.
    """
    cm = map_simple_columns(header_row, aliases or PREMIUM_ALIASES)
    norms = [normalize_header(h) if h is not None else "" for h in header_row]
    groups = []
    if group_row:
        groups = [normalize_header(h) if h is not None else "" for h in group_row]

    ppn_idxs = [i for i, n in enumerate(norms) if _is_ppn_header(n)]
    si_idxs = [i for i, n in enumerate(norms) if _looks_like_si(n)]
    prem_idxs = [i for i, n in enumerate(norms) if _looks_like_prem(n)]

    # Gross = first SI + first PREMIUM that appear before first allocation PPN
    first_ppn = min(ppn_idxs) if ppn_idxs else len(norms)
    # Prefer already-mapped gross/SI from aliases when present
    if "sum_insured" not in cm.mapping:
        gross_si = next((i for i in si_idxs if i < first_ppn), None)
        if gross_si is not None:
            cm.mapping["sum_insured"] = gross_si
    if "gross_premium" not in cm.mapping:
        gross_prem = next((i for i in prem_idxs if i < first_ppn), None)
        if gross_prem is not None:
            cm.mapping["gross_premium"] = gross_prem

    def _band_kind(g: str) -> str:
        u = g.upper()
        if any(x in u for x in ("OWN RETENTION", "RETENTION", "RETAINED", "RTNTN", "RTN%")):
            return "ret"
        if any(x in u for x in ("FAC", "FACULTATIVE")):
            return "fac"
        if any(x in u for x in ("SURP", "SURPLUS", "TREATY", "QUOTA", "QS")):
            return "sur"
        return ""

    # Allocation triples: PPN then SI/Premium (AIICO), or SI/Premium then PPN (UNITRUST).
    blocks = []
    for pi in ppn_idxs:
        si_after = next((i for i in si_idxs if pi < i <= pi + 3), None)
        pr_after = next((i for i in prem_idxs if pi < i <= pi + 3), None)
        si_before = next((i for i in reversed(si_idxs) if pi - 3 <= i < pi), None)
        pr_before = next((i for i in reversed(prem_idxs) if pi - 3 <= i < pi), None)
        ppn_kind = _band_kind(norms[pi])

        def _affinity(si_i: Optional[int], pr_i: Optional[int], _kind=ppn_kind) -> bool:
            if not _kind:
                return False
            for idx in (si_i, pr_i):
                if idx is not None and _band_kind(norms[idx]) == _kind:
                    return True
            return False

        if si_after is not None and pr_after is not None:
            if (
                si_before is not None and pr_before is not None
                and _affinity(si_before, pr_before) and not _affinity(si_after, pr_after)
            ):
                blocks.append((pi, si_before, pr_before))
            else:
                blocks.append((pi, si_after, pr_after))
        elif si_before is not None and pr_before is not None:
            blocks.append((pi, si_before, pr_before))
        elif pr_after is not None:
            blocks.append((pi, si_after, pr_after))
        elif pr_before is not None:
            blocks.append((pi, si_before, pr_before))

    def group_for(col: int) -> str:
        # Prefer band encoded in the PPN header itself (RTN%, TREATY%, RTNTN PPN)
        # so a group label sitting over the SI column to the right cannot steal it.
        own = norms[col] if col < len(norms) else ""
        if _band_kind(own):
            return own
        if not groups:
            return own
        label = ""
        for j in range(col, -1, -1):
            if j < len(groups) and groups[j]:
                label = groups[j]
                break
        return label or own

    ret_set = False
    sur_set = False
    fac_set = False
    used: set = set()
    for idx, (pi, si, pr) in enumerate(blocks):
        g = group_for(pi)
        kind = _band_kind(g)
        if kind == "ret" and not ret_set:
            cm.ret_ppn, cm.ret_si, cm.ret_prem = pi, si, pr
            ret_set = True
            used.add(idx)
        elif kind == "fac" and not fac_set:
            cm.fac_ppn, cm.fac_si, cm.fac_prem = pi, si, pr
            fac_set = True
            used.add(idx)
        elif kind == "sur" and not sur_set:
            cm.sur_ppn, cm.sur_si, cm.sur_prem = pi, si, pr
            cm.sur_label = g or "TREATY"
            sur_set = True
            used.add(idx)
        elif kind == "sur" and sur_set:
            cm.extra_treaty_blocks.append((g or f"TREATY BLOCK {idx + 1}", pi, si, pr))
            used.add(idx)
        elif kind in {"ret", "fac"}:
            cm.ignored_blocks.append(f"{g} (cols {pi + 1}-{(pr or pi) + 1})")
            used.add(idx)

    unlabelled = [blocks[i] for i in range(len(blocks)) if i not in used]
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

    # Named columns when no PPN triples were found (AXA, UNITRUST, HEIRS RTN%).
    if not ret_set and not sur_set and not fac_set:
        named = _named_band_columns(norms)
        rp, rs, rr = named["ret"]
        sp, ss, sr = named["sur"]
        fp, fs, fr = named["fac"]
        if rr is not None or rs is not None:
            cm.ret_ppn, cm.ret_si, cm.ret_prem = rp, rs, rr
            ret_set = True
        if sr is not None or ss is not None:
            cm.sur_ppn, cm.sur_si, cm.sur_prem = sp, ss, sr
            cm.sur_label = cm.sur_label or "TREATY"
            sur_set = True
        if fr is not None or fs is not None:
            cm.fac_ppn, cm.fac_si, cm.fac_prem = fp, fs, fr
            fac_set = True
        # OBLIG TREATY as extra layer when TREATY already taken
        oblig_prem = next(
            (i for i, n in enumerate(norms) if "OBLIG" in n and "PREMIUM" in n),
            None,
        )
        oblig_si = next(
            (i for i, n in enumerate(norms) if "OBLIG" in n and "SUM INSURED" in n),
            None,
        )
        if oblig_prem is not None and sur_set:
            cm.extra_treaty_blocks.append(("OBLIG TREATY", None, oblig_si, oblig_prem))

    return cm


def cell(row: Sequence[Any], idx: Optional[int]) -> Any:
    if idx is None:
        return None
    if idx < 0 or idx >= len(row):
        return None
    return row[idx]
