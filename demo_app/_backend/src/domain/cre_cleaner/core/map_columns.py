"""Fuzzy / alias map source columns → template fields.

Shared, cedant-neutral mechanism only (IMPL-20260929-04). The alias tables
below hold generic, 100%-level names; every cedant/broker-specific name,
band vocabulary or layout rule lives in that cedant's adapter
(``premium_alias_extra`` / ``claims_alias_extra`` /
``premium_layout_rules`` hooks in ``cre_cleaner.adapters``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.domain.cre_cleaner.core.normalize import normalize_header


# Canonical target → accepted normalized aliases (generic, 100%-level names
# only; cedant names come from the adapters' premium/claims_alias_extra).
# Each list is in preference order, most specific (longest) name first: when
# a header row has exact matches for two aliases of one field, the alias
# listed first wins, then the leftmost column (IMPL-20260929-04; never by a
# length score). Adapter extras are placed before these generic names.
PREMIUM_ALIASES: Dict[str, List[str]] = {
    "policy_no": [
        "POLICY NUMBER", "POLICYNUMBER", "POLICY KEY", "POLICY NO", "POLICY NO.", "POL NO",
        "POLICY",
    ],
    "insured": [
        "NAME OF THE INSURED", "NAME OF INSURED", "NAME OF ASSURED", "CUSTOMERS NAME",
        "INSURED NAME", "INSURED", "ASSURED",
    ],
    "class": ["POLICY CLASS", "RISKS TYPE", "COVER TYPE", "RISK TYPE", "POL CLASS", "CLASS"],
    "channel": ["CHANNEL"],
    "sub_channel": ["SUB CHANNEL", "SUBCHANNEL", "SUB CLASS", "SUBCLASS"],
    "uw_year": [
        "UNDERWRITING YEAR", "UDW YEAR", "UW YEAR", "U YEAR", "U/YEAR", "UW YR", "UY", "UW",
    ],
    "period_from": [
        "RISK COV FROM", "COV FROM", "COVER FROM", "PERIOD FROM", "INCEPTION",
        "EFFECTIVE DATE", "COVER START", "START DATE", "FROM",
    ],
    "period_to": [
        "RISK COV TO", "COV TO", "COVER TO", "PERIOD TO", "EXPIRY",
        "EXPIRY DATE", "COVER END", "END DATE", "TO",
    ],
    "period": [
        "PERIOD OF INSURANCE", "INSURANCE PERIOD", "PERIOD OF COVER", "COVER PERIOD", "PERIOD",
    ],
    "sum_insured": ["GROSS SUM INSURED", "TOTAL SUM INSURED", "SUM INSURED", "TSI", "SI"],
    "mpl": ["MPL PCT", "MPL %", "MPL"],
    "gross_premium": ["GROSS PREMIUM", "GROSS PREM", "PREMIUM", "GP"],
    "debit_note": ["CREDIT NOTE", "DEBIT NOTE", "DEBITNOTE", "DN"],
    "currency": ["CURRENCY", "CCY"],
}

CLAIMS_ALIASES: Dict[str, List[str]] = {
    "insured": [
        "NAME OF INSURED CLAIMANT", "INSURED CLAIMANT", "NAME OF INSURED", "INSURED NAME",
        "CLAIMANT", "INSURED",
    ],
    "class": ["POLICY CLASS", "SUB CLASS", "POL CLASS", "SUBCLASS", "CLASS"],
    "policy_no": ["POLICY NUMBER", "POLICY NO", "POLICY NO.", "POLICY"],
    "claim_no": ["CLAIM NUMBER", "CLAIMNUMBER", "CLAIM NO", "CLAIM NO."],
    "date_of_loss": [
        "DATE OF LOSS DAY MTH YEAR", "DATE OF LOSS INCIDENT", "DATE OF LOSS", "LOSS DATE",
        "DATE OF", "DOL",
    ],
    "uw_yr": ["UNDERWRITING YEAR", "UW YEAR", "U YEAR", "U/YEAR", "UW YR", "UY"],
    "period_from": [
        "RISK COV FROM", "COV FROM", "COVER FROM", "PERIOD FROM", "INCEPTION",
        "PERIOD OF COVER FROM", "START DATE", "FROM",
    ],
    "period_to": [
        "RISK COV TO", "COV TO", "COVER TO", "PERIOD TO", "EXPIRY",
        "PERIOD OF COVER TO", "END DATE", "TO",
    ],
    "period": ["PERIOD OF INSURANCE", "PERIOD OF COVER", "PERIOD"],
    "total_claims": [
        "TOTAL RESERVE AMOUNT", "TOTAL CLAIMS CLAIMS", "GROSS LOSS RESERVE",
        "TOTAL CLAIMS PAID", "TOTAL AMOUNT PAID", "TOTAL OST RESERVE", "TOTAL OUTSTANDING",
        "TOTAL CLAIM PAID", "TOTAL CLAIMS", "LOSS RESERVE", "OST RESERVE", "TOTAL CLAIM",
        "TOTAL OST",
    ],
    "amount_ret": [
        "RETENTION AMOUNT", "NET RETENTIONS", "NET LIABILITY", "OWN RETENTION", "NET RETENTION",
        "AMOUNT RET", "OWN SHARE", "OWN SHARE.",
    ],
    "amount_treaty": [
        "TREATY FIRST SURPLUS", "TREATY QUOTA SHARE", "TREATY RECOVERY", "TOTAL RECOVERY",
        "TREATY AMOUNT", "AMOUNT TREATY", "TREATY SHARE", "1ST SURPLUS", "2ND SURPLUS",
        "TTY AMOUNT", "1ST SURP", "2ND SURP", "SURPLUS", "TREATY",
    ],
    "amount_fac": ["FACULTATIVE", "FAC AMOUNT", "AMOUNT FAC", "FAC OBLIG", "FAC"],
    "details": [
        "DESCRIPTION OF LOSS", "DECSRIPTION OF LOSS", "DESCRIPTION OF LOSS",
        "DETAILS OF INCIDENT", "LOSS DESCRIPTION", "DETAILS OF LOSS", "PARTICULARS",
    ],
    "month": ["MONTH"],
    "paid_date": [
        "DATE PAID TO BE PAID", "DATE OF PAYMENT", "DATE CLAIM PAID", "PAYMENT DATE",
        "DATE PAID", "PAID DATE",
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
    # Treaty/FAC blocks whose SI/premium would have come from another block's
    # columns (e.g. retention); those cells are left blank instead of copied.
    borrowed_blocks: List[str] = field(default_factory=list)
    unmapped_headers: List[str] = field(default_factory=list)
    # Tie decisions taken by map_simple_columns (field, winner, losers, rule).
    tie_log: List[str] = field(default_factory=list)
    # Claims: further treaty-layer amount columns an adapter located
    # positionally ((col, label)); copied per row like alias-found layers.
    extra_amount_columns: List[Tuple[int, str]] = field(default_factory=list)

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
    return _alias_score(norm, aliases) > (0, 0)


EXACT_SCORE = 1000


def _alias_score(norm: str, aliases: List[str]) -> Tuple[int, int]:
    """Comparable match score ``(tier, order)``; ``(0, 0)`` = no match.

    An exact match scores ``(EXACT_SCORE, -alias_index)`` — never by alias
    length: between two exact matches the alias listed first wins (lists are
    in preference order). Partial matches keep the containment tiers
    (header-more-specific 100+len, alias-more-specific 50+len, substring
    10+len) with order 0; any exact match beats any partial one.
    """
    best: Tuple[int, int] = (0, 0)
    for idx, a in enumerate(aliases):
        an = normalize_header(a)
        if not an or not norm:
            continue
        if norm == an:
            best = max(best, (EXACT_SCORE, -idx))
            continue
        # Header more specific than alias: "POLICY NUMBER" vs "POLICY"
        if len(an) >= 4 and (
            norm.startswith(an + " ")
            or norm.endswith(" " + an)
            or f" {an} " in f" {norm} "
        ):
            best = max(best, (100 + len(an), 0))
            continue
        # Alias more specific than header: "NAME OF INSURED" vs "INSURED"
        if an.endswith(" " + norm) and len(norm) >= 4:
            prefix = an[: -(len(norm) + 1)]
            # Reject SUB CLASS → CLASS, OWN RETENTION → RETENTION (bare), etc.
            first = prefix.split()[0] if prefix else ""
            if first in _SHARE_PREFIX_REJECT:
                continue
            best = max(best, (50 + len(norm), 0))
            continue
        if an in norm and len(an) >= 4:
            best = max(best, (10 + len(an), 0))
    return best


def map_simple_columns(
    headers: Sequence[Any],
    alias_table: Dict[str, List[str]],
    *,
    exclude: Optional[Callable[[str, str], bool]] = None,
) -> ColumnMap:
    """Field → first/best column. Fields claim columns in alias-table order.

    Ties: equal scores are resolved by alias-list order (exact matches), then
    by column position (leftmost). Every field that had more than one exact
    candidate column is written to ``cm.tie_log`` so the choice is auditable.
    ``exclude(field, normalized_header)`` lets an adapter veto a column for a
    field (e.g. a cedant's share-of-SI column as TOTAL SUM INSURED).
    """
    cm = ColumnMap()
    norms = [normalize_header(h) if h is not None else "" for h in headers]
    used = set()
    for field, aliases in alias_table.items():
        best_i, best_score = None, (0, 0)
        exact_cands: List[Tuple[int, Tuple[int, int]]] = []
        for i, n in enumerate(norms):
            if i in used or not n:
                continue
            if field in _AMOUNT_FIELDS and is_share_header(n):
                continue
            if exclude is not None and exclude(field, n):
                continue
            score = _alias_score(n, aliases)
            if score[0] >= EXACT_SCORE:
                exact_cands.append((i, score))
            if score > best_score:
                best_score = score
                best_i = i
        if best_i is not None:
            cm.mapping[field] = best_i
            used.add(best_i)
            if len(exact_cands) > 1:
                others = [f"col {i + 1} {norms[i]!r}" for i, _sc in exact_cands if i != best_i]
                by_alias = any(sc != best_score for _i, sc in exact_cands if _i != best_i)
                cm.tie_log.append(
                    f"{field}: col {best_i + 1} {norms[best_i]!r} over {', '.join(others)} "
                    f"({'alias-list order' if by_alias else 'column position'})"
                )
    cm.unmapped_headers = [norms[i] for i in range(len(norms)) if i not in used and norms[i]]
    return cm


REQUIRED_PREMIUM_FIELDS = ("policy_no", "insured", "period_from", "period_to", "gross_premium")
REQUIRED_CLAIMS_FIELDS = ("policy_no", "insured", "period_from", "period_to", "total_claims")


def missing_required_fields(cmap: ColumnMap, kind: str) -> List[str]:
    """Template fields the manual requires that this header did not map.

    A single PERIOD column supplies both Insurance Period From and To, so
    those two columns are not required on their own when ``period`` mapped.
    """
    fields = REQUIRED_CLAIMS_FIELDS if kind == "claims" else REQUIRED_PREMIUM_FIELDS
    period_covers = cmap.get("period") is not None
    missing: List[str] = []
    for field in fields:
        if field in ("period_from", "period_to") and period_covers:
            continue
        if cmap.get(field) is None:
            missing.append(field)
    return missing


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

    Mechanism; used unless an adapter opts out (``band_subrow_layout``)::

      row1: … GROSS LOSS RESERVE | RETENTION | | TREATY |
      row2: …                    | %         | AMOUNT | % | AMOUNT
    → RETENTION PPN, RETENTION AMOUNT, TREATY PPN, TREATY AMOUNT

      row1: … | RETENTION | | | TREATY |
      row2: … | SUM INSURED | GROSS PREMIUM | % | SUM INSURED | …
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


class PremiumLayoutRules:
    """Generic premium allocation-band vocabulary and layout rules.

    The shared engine (``detect_premium_allocation_blocks``) asks these hooks
    which headers are PPN / SI / premium columns, which band a label belongs
    to, and how a PPN column pairs with its SI/premium columns. This base
    class knows only generic reinsurance wording; a cedant adapter subclasses
    it (``BaseAdapter.premium_layout_rules``) to add its own headers and
    layouts. Cedant rules are never added here.
    """

    PPN_EXACT = frozenset({
        "PPN", "PPN %", "PROPORTION", "PROPORTION %", "%",
        "TREATY%", "TREATY %", "TREATY PPN", "TREATY PPN%",
    })
    PPN_PCT_BAND_WORDS: Tuple[str, ...] = ("RET", "TREATY", "FAC", "QUOTA", "SURP")
    SI_EXACT = frozenset({
        "SUM INSURED", "TOTAL SUM INSURED", "QUOTA SHARE SUM INSURED", "TREATY SUM INSURED",
        "FACULTATIVE SUM INSURED", "SI",
    })
    PREM_EXACT = frozenset({
        "PREMIUM", "GROSS PREMIUM", "QUOTA SHARE PREMIUM", "TREATY PREMIUM",
        "FACULTATIVE PREMIUM", "RETENTION GROSS PREMIUM", "TREATY GROSS PREMIUM",
        "RETENTION PREMIUM",
    })
    RET_BAND_WORDS: Tuple[str, ...] = ("OWN RETENTION", "RETENTION", "RETAINED")
    FAC_BAND_WORDS: Tuple[str, ...] = ("FAC", "FACULTATIVE")
    SUR_BAND_WORDS: Tuple[str, ...] = ("SURP", "SURPLUS", "TREATY", "QUOTA", "QS")

    # --- header vocabulary -------------------------------------------------
    def is_ppn_header(self, norm: str) -> bool:
        if not norm:
            return False
        if norm in self.PPN_EXACT:
            return True
        if norm.endswith(" PPN") or " PPN " in f" {norm} " or norm.endswith(" PPN%"):
            return True
        if norm.endswith("%") and any(x in norm for x in self.PPN_PCT_BAND_WORDS):
            return True
        return False

    def looks_like_si(self, norm: str) -> bool:
        return norm in self.SI_EXACT or norm.endswith(" SUM INSURED") or norm.endswith(" SI")

    def looks_like_prem(self, norm: str) -> bool:
        if not norm or is_share_header(norm):
            return False
        if norm in self.PREM_EXACT:
            return True
        return norm.endswith(" PREMIUM") or norm.endswith(" PREM") or norm.endswith(" GROSS PREMIUM")

    def band_kind(self, label: str) -> str:
        u = label.upper()
        if any(x in u for x in self.RET_BAND_WORDS):
            return "ret"
        if any(x in u for x in self.FAC_BAND_WORDS):
            return "fac"
        if any(x in u for x in self.SUR_BAND_WORDS):
            return "sur"
        return ""

    # --- layout hooks ------------------------------------------------------
    def block_for_ppn(
        self, pi: int, norms: List[str], si_idxs: List[int], prem_idxs: List[int],
    ) -> Optional[Tuple[int, Optional[int], Optional[int]]]:
        """(ppn, si, premium) columns for the PPN column ``pi``: SI/premium
        within three columns after (PPN-first layout) or before it
        (SI/premium-first layout); band affinity breaks the tie."""
        si_after = next((i for i in si_idxs if pi < i <= pi + 3), None)
        pr_after = next((i for i in prem_idxs if pi < i <= pi + 3), None)
        si_before = next((i for i in reversed(si_idxs) if pi - 3 <= i < pi), None)
        pr_before = next((i for i in reversed(prem_idxs) if pi - 3 <= i < pi), None)
        ppn_kind = self.band_kind(norms[pi])

        def _affinity(si_i: Optional[int], pr_i: Optional[int]) -> bool:
            if not ppn_kind:
                return False
            return any(idx is not None and self.band_kind(norms[idx]) == ppn_kind
                       for idx in (si_i, pr_i))

        if si_after is not None and pr_after is not None:
            if (
                si_before is not None and pr_before is not None
                and _affinity(si_before, pr_before) and not _affinity(si_after, pr_after)
            ):
                return (pi, si_before, pr_before)
            return (pi, si_after, pr_after)
        if si_before is not None and pr_before is not None:
            return (pi, si_before, pr_before)
        if pr_after is not None:
            return (pi, si_after, pr_after)
        if pr_before is not None:
            return (pi, si_before, pr_before)
        return None

    def adjust_band(self, kind: str, label: str, ppn_norm: str, ret_set: bool) -> Tuple[str, str]:
        """Hook to re-band a block (default: unchanged)."""
        return kind, label

    def named_bands(
        self, norms: List[str],
    ) -> Optional[Dict[str, Tuple[Optional[int], Optional[int], Optional[int]]]]:
        """Labelled-band fallback when no PPN triples were found: dict of
        ret/sur/fac → (ppn, si, premium). Generic rules have none."""
        return None

    def extra_named_layers(self, norms: List[str], cm: "ColumnMap") -> None:
        """Hook: further layers after a labelled-band fallback (default none)."""
        return None


GENERIC_PREMIUM_RULES = PremiumLayoutRules()


def find_named_column(
    norms: Sequence[str], needles: Sequence[str], skip: Callable[[str], bool] = lambda n: False,
) -> Optional[int]:
    """First column whose header equals one of ``needles``; else the first
    containing one (never a share/% header, never one ``skip`` rejects).
    Vocabulary comes from the caller (an adapter's layout rules)."""
    for i, n in enumerate(norms):
        if n in needles:
            return i
    for i, n in enumerate(norms):
        for needle in needles:
            if n == needle or n.endswith(" " + needle) or needle in n:
                if is_share_header(n) or skip(n):
                    continue
                return i
    return None


def _drop_borrowed_columns(cm: ColumnMap, norms: Sequence[str]) -> None:
    """Each band copies SI/premium only from its own columns (Cleaning Manual:
    copy source figures, never borrow/calculate). A treaty, FAC or extra layer
    block pointing at a column already owned by an earlier block (typically
    retention, when the layer's own SI/premium headers were not recognised)
    gets that cell blanked and is recorded in ``cm.borrowed_blocks``."""
    owned: Dict[int, str] = {}

    def claim(label: str, ppn: Optional[int], si: Optional[int], pr: Optional[int]):
        """Returns (ppn, si, pr) with borrowed columns blanked. A block left with
        no SI/premium column of its own is not mapped at all (its % column is
        named in the exception for review)."""
        keep = []
        borrowed = False
        for kind, col in (("SI", si), ("PREMIUM", pr)):
            if col is not None and col in owned:
                borrowed = True
                cm.borrowed_blocks.append(
                    f"{label} {kind} (PPN col {'' if ppn is None else ppn + 1}) would reuse "
                    f"col {col + 1} {norms[col] if col < len(norms) else ''!r} of {owned[col]}"
                )
                keep.append(None)
            else:
                keep.append(col)
        if borrowed and keep == [None, None]:
            if ppn is not None:
                cm.borrowed_blocks.append(
                    f"{label} block not mapped: PPN col {ppn + 1} "
                    f"{norms[ppn] if ppn < len(norms) else ''!r} has no SI/premium columns of its own"
                )
            return None, None, None
        for col in keep:
            if col is not None:
                owned[col] = label
        return ppn, keep[0], keep[1]

    for c in (cm.ret_si, cm.ret_prem):
        if c is not None:
            owned[c] = "RETENTION"
    cm.sur_ppn, cm.sur_si, cm.sur_prem = claim("TREATY", cm.sur_ppn, cm.sur_si, cm.sur_prem)
    cm.fac_ppn, cm.fac_si, cm.fac_prem = claim("FAC", cm.fac_ppn, cm.fac_si, cm.fac_prem)
    extra = []
    for label, pi, si, pr in cm.extra_treaty_blocks:
        pi2, si2, pr2 = claim(label or "TREATY LAYER", pi, si, pr)
        if (pi2, si2, pr2) != (None, None, None):
            extra.append((label, pi2, si2, pr2))
    cm.extra_treaty_blocks = extra


def detect_premium_allocation_blocks(
    header_row: Sequence[Any],
    group_row: Optional[Sequence[Any]] = None,
    aliases: Optional[Dict[str, List[str]]] = None,
    rules: Optional[PremiumLayoutRules] = None,
    *,
    exclude: Optional[Callable[[str, str], bool]] = None,
) -> ColumnMap:
    """Simple columns plus RET / TREATY / FAC allocation bands.

    Bands are PPN columns paired with their SI / premium columns
    (``rules.block_for_ppn``), labelled by the PPN header itself or the group
    (band) row above it. ``rules`` carries the vocabulary and layout hooks —
    generic by default; each adapter passes its own.
    """
    rules = rules or GENERIC_PREMIUM_RULES
    cm = map_simple_columns(header_row, aliases or PREMIUM_ALIASES, exclude=exclude)
    norms = [normalize_header(h) if h is not None else "" for h in header_row]
    groups = []
    if group_row:
        groups = [normalize_header(h) if h is not None else "" for h in group_row]

    ppn_idxs = [i for i, n in enumerate(norms) if rules.is_ppn_header(n)]
    si_idxs = [i for i, n in enumerate(norms) if rules.looks_like_si(n)]
    prem_idxs = [i for i, n in enumerate(norms) if rules.looks_like_prem(n)]

    # Gross = first SI + first PREMIUM that appear before first allocation PPN
    first_ppn = min(ppn_idxs) if ppn_idxs else len(norms)
    # Prefer already-mapped gross/SI from aliases when present
    if "sum_insured" not in cm.mapping:
        gross_si = next((i for i in si_idxs if i < first_ppn
                         and not (exclude and exclude("sum_insured", norms[i]))), None)
        if gross_si is not None:
            cm.mapping["sum_insured"] = gross_si
    if "gross_premium" not in cm.mapping:
        gross_prem = next((i for i in prem_idxs if i < first_ppn
                           and not (exclude and exclude("gross_premium", norms[i]))), None)
        if gross_prem is not None:
            cm.mapping["gross_premium"] = gross_prem

    blocks = []
    for pi in ppn_idxs:
        b = rules.block_for_ppn(pi, norms, si_idxs, prem_idxs)
        if b is not None:
            blocks.append(b)

    def group_for(col: int) -> str:
        # Prefer band encoded in the PPN header itself (e.g. TREATY%)
        # so a group label sitting over the SI column to the right cannot steal it.
        own = norms[col] if col < len(norms) else ""
        if rules.band_kind(own):
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
        kind = rules.band_kind(g)
        kind, g = rules.adjust_band(kind, g, norms[pi], ret_set)
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

    _drop_borrowed_columns(cm, norms)

    # Labelled-band fallback (adapter-owned) when no PPN triples were found.
    if not ret_set and not sur_set and not fac_set:
        named = rules.named_bands(norms)
        if named:
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
            if sur_set:
                rules.extra_named_layers(norms, cm)

    refresh_unmapped(cm, header_row)
    return cm


def refresh_unmapped(cmap: ColumnMap, headers: Sequence[Any], *extra: Optional[int]) -> None:
    """Drop columns the simple map or a band already claimed.

    ``map_simple_columns`` records every non-alias header as unmapped, including
    RET/TREATY/FAC columns that the allocation pass then assigns.
    """
    consumed = {i for i in cmap.mapping.values() if i is not None}
    for idx in extra:
        if idx is not None:
            consumed.add(idx)
    for cols in (
        (cmap.ret_ppn, cmap.ret_si, cmap.ret_prem),
        (cmap.sur_ppn, cmap.sur_si, cmap.sur_prem),
        (cmap.fac_ppn, cmap.fac_si, cmap.fac_prem),
    ):
        consumed.update(i for i in cols if i is not None)
    for block in cmap.extra_treaty_blocks:
        consumed.update(i for i in block[1:] if isinstance(i, int))
    norms = [normalize_header(h) if h is not None else "" for h in headers]
    cmap.unmapped_headers = [
        norms[i] for i in range(len(norms)) if i not in consumed and norms[i]
    ]


def cell(row: Sequence[Any], idx: Optional[int]) -> Any:
    if idx is None:
        return None
    if idx < 0 or idx >= len(row):
        return None
    return row[idx]
