"""ZENITH adapters (ARK, Jordans Global) — first-pass, unverified.

One workbook per quarter (or one per treaty: 2ND SURPLUS / LOCAL via ARK,
FOREIGN / 1ST SURPLUS via Jordans) with a premium tab and a claims tab per
class (FIRE, MARINE CARGO, ENGINEERING, MARINE HULL, MONEY, FG, TERRORISM,
BOND) plus treaty summary tabs.

* The tab names the treaty class. Claims tabs also carry a CLASS column with
  the policy sub-class ('PLANT ALL RISK', 'COMPUTER/EEI - ALL RISK') or a
  code ('04 - Marine' on both the cargo and the hull claims tab), so the tab
  class wins on claims too.
* Tab names add treaty / bordereau words around the class ('FG PREM
  BORDEREAU', 'MC 2ND SURPLUS', 'FG(SURPLUS)'); they are dropped before the
  class is read. MC / MH are the Marine Cargo / Marine Hull tabs (named
  'MARINE CARGO 2ND SURPLUS' / 'MARINE HULL' in the other quarters); 'MARINE
  CAR CLAIMS PAID' (Q4 2020) is the cargo claims tab cut short.

Premium bands. 2020-2023 tabs name the bands in the row above a header of
bare '%' | 'SUM INSURED' | 'PREMIUM' cells, in either order (PPN first, or
SI | PREMIUM | % with the band name over the SI cell); those cells are
prefixed with their band name. Later tabs name each cell ('OWN RETENTION
PPN', '1ST SURPLUS TREATY SUM INSURED'). Retention is also 'ZGIC %' /
'ZGIC CESSION %' / 'ZGIC RTN %' | 'ZGIC SUM INSURED' | 'ZGIC PREMIUM', or
'ZGIC RETENTION' (the share) and 'OWN RETENTION' (the SI, after OWN
RETENTION PPN). A PPN column pairs with the SI / premium columns of the same
band name. A bare 'PPN' / '%' outside a band, 'SUM ASSURED PPN' and 'PML %'
are not band shares. FAC OBLIG is an obligatory treaty: a further treaty
layer, never the FAC band (as AXA's OBLIG TREATY).

Gross TSI / premium: 100% columns ('100% SUM INSURED', 'SUM ASSURED',
'TOTAL PREMIUM') unless tsi_gp_basis is our_share ('OUR SUM INSURED',
'OUR SHARE PREMIUM'); the bands split Zenith's share, so on co-insured
policies (OUR PROP % < 100) a 100% gross does not equal the band sum.

Claims. Band '%' / 'PPN' | 'AMOUNT' pairs are named in the row above
(RETENTION | 1ST SURPLUS | 2ND SURPLUS | FACULTATIVE). The 1st surplus is the
TREATY band; 2nd surplus, XOL and FAC OBLIG amounts are further layers.
Recovery / offset / salvage columns are recovery tracking, not bands. PERIOD
and CLM PROCESS PERIOD hold years, not the cover period; the paid date is a
date column ('CLAIM PAID' is the amount).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.core.detect import SheetTypeRules
from src.domain.cre_cleaner.core.map_columns import (
    PREMIUM_ALIASES,
    ColumnMap,
    PremiumLayoutRules,
    merged_aliases,
    normalize_header,
)
from src.domain.cre_cleaner.core.normalize import parse_number

ZENITH_SHEET_RULES = SheetTypeRules(class_noise_words=frozenset({
    "PREM", "PREMIUM", "BORD", "BORDEREAU", "BORDEREAUX", "CLAIM", "CLAIMS", "PAID",
    "SURPLUS", "SURPLS", "SURP", "SUCLAIMS", "1ST", "2ND", "TREATY", "AC",
}))
ZENITH_CLASS_EXTRA = {"MC": "Marine Cargo", "MH": "Marine Hull", "MARINE CAR": "Marine Cargo"}

_GROSS_100 = {
    "sum_insured": ["100% SUM INSURED", "TOTAL SUM INSURED", "SUM ASSURED"],
    "gross_premium": ["100% PREMIUM", "GROSS PREMIUM", "TOTAL PREMIUM"],
}
_GROSS_OUR_SHARE = {
    "sum_insured": ["OUR SUM INSURED", "OUR SHARE SUM INSURED", "OUR OWN SHARE SUM INSURED"],
    "gross_premium": ["OUR PREMIUM", "OUR SHARE PREMIUM", "OUR SHARE OF PREMIUM"],
}

ZENITH_CLAIMS_ALIAS_EXTRA = {"amount_ret": ["RETAINED AMOUNT"]}

_BAND_WORDS = re.compile(r"RETENTION|RETAINED|TREATY|SURPLUS|SURP|FAC|QUOTA")
_PREMIUM_BARE = {"%", "PPN", "SUM INSURED", "SI", "PREMIUM"}
_CLAIMS_BARE = {"%", "PPN", "AMOUNT"}
_TYPOS = (("SUM INSSURED", "SUM INSURED"), ("SUM ISNURED", "SUM INSURED"),
          ("SUM INUSRED", "SUM INSURED"), ("ZGIC RTN", "ZGIC RETENTION"))
_OBLIG = re.compile(r"^FAC(?:ULTATIVE)? OBLIG(?:ATORY)?(?: TREATY)?\b")
_ROLE_TAIL = re.compile(r"\s*(?:PPN\s*%?|CESSION\s*%|%|SUM INSURED|SI|PREMIUM|PREM)$")
_NOT_BAND_SHARE = ("SUM ASSURED", "PML", "MPL", "BRANCH", "OFFICER", "OUR PROP", "OUR SHARE", "COIN")
_RECOVERY = re.compile(r"RECOVER|OFFSET|SALVAGE|SUBROGATION")
_NOT_COVER_PERIOD = {"PERIOD", "CLM PROCESS PERIOD"}
_PURE_BAND = re.compile(
    r"^(?:RETENTION|RETAINED|(?:1ST |2ND )?SURPLUS(?: TREATY)?|TREATY(?: (?:1ST|2ND) SURPLUS)?"
    r"|FAC(?:ULTATIVE)?|QUOTA SHARE|XOL(?: TREATY)?|OBLIG TREATY)$"
)


def _band_label(cell: Any) -> str:
    """Band name from a band-row cell ('' when the cell names no band)."""
    n = normalize_header(cell) if cell is not None else ""
    if not n or not _BAND_WORDS.search(n):
        return ""
    if "OBLIG" in n or "OBLIQ" in n:
        return "OBLIG TREATY"
    return "RETENTION" if n == "RETAINED" else n


def band_labels(band_row: Optional[Sequence[Any]]) -> Dict[int, str]:
    return {j: lab for j, c in enumerate(band_row or []) if (lab := _band_label(c))}


def _prefix_bare_cells(norms: List[str], labels: Dict[int, str], bare: set, width: int) -> List[str]:
    """Prefix bare cells under a band-row label (up to ``width`` cells, never
    past the next label) with that band name."""
    out = list(norms)
    starts = sorted(labels)
    for k, j in enumerate(starts):
        stop = min(j + width, starts[k + 1] if k + 1 < len(starts) else j + width)
        for i in range(j, min(stop, len(out))):
            if out[i] in bare:
                out[i] = f"{labels[j]} {out[i]}"
    return out


def _premium_cell(n: str) -> str:
    for bad, good in _TYPOS:
        n = n.replace(bad, good)
    n = re.sub(r"\bTREAT\b", "TREATY", n)
    # Two header cells run together: 'FAC OUTWARD SUM INSURED FAC OUTWARD PPN% S I'.
    n = re.sub(r"(SUM INSURED)\s+\S.*PPN%?\s*S I$", r"\1", n)
    n = re.sub(r"\b(TREATY|SURPLUS) INSURED$", r"\1 SUM INSURED", n)
    n = re.sub(r" PPN SUM INSURED$", " PPN", n)
    if n.startswith("ZGIC ") and "RETENTION" not in n:
        n = "ZGIC RETENTION " + n[len("ZGIC "):]
    return _OBLIG.sub("OBLIG TREATY", n)


def zenith_premium_header(header: Sequence[Any], labels: Dict[int, str]) -> List[str]:
    norms = [_premium_cell(normalize_header(h)) if h is not None else "" for h in header]
    prev = ""
    for i, n in enumerate(norms):
        if n == "ZGIC RETENTION":
            norms[i] = "ZGIC RETENTION PPN"
        elif n == "OWN RETENTION":
            norms[i] = "OWN RETENTION SUM INSURED" if prev == "OWN RETENTION PPN" else "OWN RETENTION PPN"
        if n:
            prev = norms[i]
    # The share cell under a band label is sometimes unlabelled ('' | SUM
    # INSURED | PREMIUM under RETENTION).
    for j in labels:
        if j < len(norms) and not norms[j] and {"SUM INSURED", "PREMIUM"} & set(norms[j + 1:j + 3]):
            norms[j] = "PPN"
    return _prefix_bare_cells(norms, labels, _PREMIUM_BARE, 3)


def zenith_claims_header(header: Sequence[Any], labels: Dict[int, str]) -> List[str]:
    norms = [_OBLIG.sub("OBLIG TREATY", normalize_header(h)) if h is not None else "" for h in header]
    # A band named over (or in) a cell whose right neighbour is unlabelled:
    # the two columns are that band's share and amount.
    named = dict(labels)
    for j, n in enumerate(norms):
        if _PURE_BAND.match(n) and j not in named:
            named[j] = _band_label(n)
    for j, lab in sorted(named.items()):
        if j + 1 < len(norms) and not norms[j + 1] and (not norms[j] or _PURE_BAND.match(norms[j])):
            norms[j], norms[j + 1] = f"{lab} PPN", f"{lab} AMOUNT"
    return _prefix_bare_cells(norms, labels, _CLAIMS_BARE, 2)


def _claims_band_part(norm: str) -> Tuple[str, str]:
    """Exact band identity and role, after source group-header preparation."""
    match = re.fullmatch(r"(.+?)\s+(AMOUNT|PAYMENT|PPN\s*%?|%)", norm)
    if not match:
        return "", ""
    band, role = match.groups()
    band = {"RETAINED": "RETENTION", "FAC": "FACULTATIVE",
            "SURPLUS": "1ST SURPLUS", "SURPLUS TREATY": "1ST SURPLUS",
            "1ST SURPLUS TREATY": "1ST SURPLUS",
            "2ND SURPLUS TREATY": "2ND SURPLUS"}.get(band, band)
    if not _BAND_WORDS.search(band):
        return "", ""
    return band, "amount" if role in {"AMOUNT", "PAYMENT"} else "ppn"


def _band_name(n: str) -> str:
    prev = None
    while prev != n:
        prev, n = n, _ROLE_TAIL.sub("", n).strip()
    return n


class ZenithPremiumRules(PremiumLayoutRules):
    def is_ppn_header(self, norm: str) -> bool:
        if norm in {"PPN", "%"} or any(w in norm for w in _NOT_BAND_SHARE):
            return False
        return super().is_ppn_header(norm)

    def block_for_ppn(
        self, pi: int, norms: List[str], si_idxs: List[int], prem_idxs: List[int],
    ) -> Optional[Tuple[int, Optional[int], Optional[int]]]:
        band = _band_name(norms[pi])

        def same_band(idxs: List[int]) -> Optional[int]:
            near = [i for i in idxs if 0 < abs(i - pi) <= 3 and _band_name(norms[i]) == band]
            return min(near, key=lambda i: abs(i - pi)) if near else None

        si, pr = same_band(si_idxs), same_band(prem_idxs)
        if band and (si is not None or pr is not None):
            return pi, si, pr
        return super().block_for_ppn(pi, norms, si_idxs, prem_idxs)


def _oblig_not_primary(cm: ColumnMap) -> None:
    """An OBLIG TREATY block found before the surplus bands moves to the
    further layers; the first surplus layer becomes the TREATY band."""
    if "OBLIG" not in cm.sur_label:
        return
    k = next((k for k, b in enumerate(cm.extra_treaty_blocks) if "OBLIG" not in b[0]), None)
    if k is None:
        return
    label, pi, si, pr = cm.extra_treaty_blocks[k]
    cm.extra_treaty_blocks[k] = (cm.sur_label, cm.sur_ppn, cm.sur_si, cm.sur_prem)
    cm.sur_label, cm.sur_ppn, cm.sur_si, cm.sur_prem = label, pi, si, pr


def _complete_retention(cm: ColumnMap, norms: List[str]) -> None:
    """'OWN RETENTION PPN | OWN RETENTION SUM INSURED' (no premium) before a
    full 'ZGIC RETENTION' PPN / SI / premium block: the full block is the
    retention band; the partial one is recorded as ignored."""
    if cm.ret_ppn is None or cm.ret_prem is not None:
        return
    for pi, n in enumerate(norms):
        if pi == cm.ret_ppn or "RETENTION" not in n or not ZenithPremiumRules().is_ppn_header(n):
            continue
        band = _band_name(n)
        near = [i for i in range(max(0, pi - 3), min(len(norms), pi + 4))
                if i != pi and _band_name(norms[i]) == band]
        si = next((i for i in near if norms[i].endswith(("SUM INSURED", " SI"))), None)
        pr = next((i for i in near if norms[i].endswith(("PREMIUM", " PREM"))), None)
        if si is None or pr is None:
            continue
        old = f"{norms[cm.ret_ppn]} (cols {cm.ret_ppn + 1}-{(cm.ret_si or cm.ret_ppn) + 1})"
        cm.ignored_blocks = [b for b in cm.ignored_blocks if f"(cols {pi + 1}-" not in b] + [old]
        cm.ret_ppn, cm.ret_si, cm.ret_prem = pi, si, pr
        return


def _numeric_cells(row: Sequence[Any]) -> int:
    return sum(parse_number(c) is not None for c in row if c not in (None, ""))


def _data_follows(raw_rows: List[List[Any]], at: int) -> bool:
    return any(_numeric_cells(r) >= 3 for r in raw_rows[at + 1:at + 4])


def _cell(c: Any) -> str:
    return normalize_header(c) if c is not None else ""


def _title_row(raw_rows: List[List[Any]], header_at: int) -> Optional[List[Any]]:
    """Title block row above a premium header: policy / insured labels and
    two or more bare band names, with no data rows in between."""
    for k in range(header_at - 1, max(header_at - 12, -1), -1):
        row = raw_rows[k]
        if _numeric_cells(row) >= 3:
            return None
        norms = [_cell(c) for c in row]
        joined = " ".join(norms)
        if (sum(bool(_PURE_BAND.match(n)) for n in norms) >= 2
                and "POLICY" in joined and "INSURED" in joined):
            return row
    return None


class _ZenithAdapter(QuarterlyWorkbookAdapter):
    cedant = "ZENITH"
    verified = False
    sheet_type_rules = ZENITH_SHEET_RULES
    claims_sheet_class_first = True
    class_label_extra = ZENITH_CLASS_EXTRA
    class_label_exact_only = frozenset(ZENITH_CLASS_EXTRA)
    premium_layout_rules = ZenithPremiumRules()
    claims_alias_extra = ZENITH_CLAIMS_ALIAS_EXTRA

    def premium_aliases(self) -> Dict[str, List[str]]:
        first, second = _GROSS_100, _GROSS_OUR_SHARE
        if self.setting("tsi_gp_basis") == "our_share":
            first, second = second, first
        return merged_aliases(PREMIUM_ALIASES, {k: first[k] + second[k] for k in first})

    def map_premium_columns(
        self,
        header: Sequence[Any],
        group_row: Optional[Sequence[Any]],
        *,
        path: Optional[Path] = None,
        sheet: str = "",
        exceptions: Optional[list] = None,
    ) -> ColumnMap:
        # A header repeated further down a tab has no band row above it; it
        # reuses the bands named over the first header of that tab.
        cache = self.__dict__.setdefault("_band_rows", {})
        key = (str(path), sheet, tuple(normalize_header(h) if h is not None else "" for h in header))
        labels = band_labels(group_row)
        if labels:
            cache[key] = labels
        else:
            labels = cache.get(key, {})
        norms = zenith_premium_header(header, labels)
        cm = self.detect_premium_allocation_blocks(norms, group_row)
        _oblig_not_primary(cm)
        _complete_retention(cm, norms)
        return cm

    def premium_exclude(self, field: str, norm: str) -> bool:
        # Gross TSI / premium are never a band's column.
        return field in ("sum_insured", "gross_premium") and bool(_BAND_WORDS.search(norm))

    def premium_header_row(self, raw_rows: List[List[Any]], found: int) -> int:
        # A title block of band names over no data ('RETENTION | 1ST SURPLUS
        # TREATY' with the bordereau banner below) precedes the real header.
        if _data_follows(raw_rows, found):
            return found
        for j in range(found + 1, min(found + 16, len(raw_rows) - 1)):
            norms = [normalize_header(c) if c is not None else "" for c in raw_rows[j]]
            if (sum(n in ("SUM INSURED", "SI") for n in norms) >= 2
                    and any("INSURED" in n and n not in ("SUM INSURED", "SI") for n in norms)
                    and _data_follows(raw_rows, j)):
                return j
        return found

    def premium_header_cells(
        self, raw_rows: List[List[Any]], header_at: int, header: Sequence[Any],
        group_row: Optional[Sequence[Any]],
    ) -> Tuple[List[Any], Optional[Sequence[Any]]]:
        header = list(header)
        if not band_labels(group_row) and not band_labels(header):
            # The band row can sit above a 'UW YEAR 2021' line instead of
            # directly over the header.
            for k in range(header_at - 2, max(header_at - 4, -1), -1):
                if len(band_labels(raw_rows[k])) >= 2 and _numeric_cells(raw_rows[k]) == 0:
                    group_row = raw_rows[k]
                    break
        # Under a title block ('Policy No. | Insured Name | Period From | … |
        # RETENTION | 1ST SURPLUS TREATY') the real header renames the policy
        # columns ('Class' over the policy numbers); the title names them.
        title = _title_row(raw_rows, header_at)
        if title is not None:
            first_band = min(j for j, c in enumerate(title) if _PURE_BAND.match(_cell(c)))
            for i in range(min(first_band, len(header))):
                if _cell(title[i]):
                    header[i] = title[i]
        return header, group_row

    def claims_header_cells(
        self, header: Sequence[Any], band_row: Optional[Sequence[Any]],
        data_rows: Sequence[Sequence[Any]] = (), notes: Optional[list] = None,
    ) -> List[Any]:
        # A header repeated further down a tab has no band row above it; it
        # reuses the bands named over an identical earlier header.
        cache = self.__dict__.setdefault("_claims_band_rows", {})
        key = tuple(normalize_header(h) if h is not None else "" for h in header)
        labels = band_labels(band_row)
        if labels:
            cache[key] = labels
        else:
            labels = cache.get(key, {})
        return zenith_claims_header(header, labels)

    def claims_band_row(
        self, raw_rows: List[List[Any]], header_at: int, band_row: Optional[Sequence[Any]],
    ) -> Optional[Sequence[Any]]:
        # Bands over a '%' | 'Amount' row, both above the field header: the
        # band names are two rows up.
        if band_labels(band_row) or header_at < 2:
            return band_row
        above = raw_rows[header_at - 2]
        labels = band_labels(above)
        sub = [normalize_header(c) if c is not None else "" for c in band_row or []]
        if labels and all(j + 1 < len(sub) and sub[j] in ("%", "PPN") and sub[j + 1] == "AMOUNT"
                          for j in labels):
            return above
        return band_row

    def claims_exclude(self, field: str, norm: str) -> bool:
        # A bare 'AMOUNT' / band name names no band amount by itself.
        if field in ("amount_ret", "amount_treaty", "amount_fac") and (
                norm == "AMOUNT" or _PURE_BAND.match(norm)):
            return True
        if field == "amount_treaty":
            return bool(re.search(r"XOL|OBLIG", norm) or _RECOVERY.search(norm))
        if field == "amount_fac":
            return "OBLIG" in norm or bool(_RECOVERY.search(norm))
        if field == "paid_date":
            return "DATE" not in norm and not re.search(r"\bDT\b", norm)
        if field == "period":
            return norm in _NOT_COVER_PERIOD
        return False

    def map_claims_columns(
        self, header: Sequence[Any], aliases: Dict[str, List[str]], sheet_type: str = "",
    ) -> ColumnMap:
        cm = super().map_claims_columns(header, aliases, sheet_type)
        parts = [_claims_band_part(normalize_header(h)) for h in header]
        # Preserve the existing amount-band selection. In particular, a
        # second-surplus/XOL/obligatory share must not fill first surplus PPN.
        for field in ("ret", "treaty", "fac"):
            amount = cm.get(f"amount_{field}")
            if amount is None:
                continue
            band, role = parts[amount]
            if role != "amount":
                continue
            cm.mapping.pop(f"ppn_{field}", None)
            shares = [i for i, part in enumerate(parts) if part == (band, "ppn")]
            if len(shares) == 1:
                cm.mapping[f"ppn_{field}"] = shares[0]
            elif shares:
                cm.tie_log.append(f"ppn_{field}: ambiguous {band} share columns "
                                  f"{[i + 1 for i in shares]}; left unmapped")
        # Recovery columns match treaty names ('TOTAL RECOVERY', 'RECOVERY
        # TREATY') but are not band amounts: claimed here so they are not
        # added as further treaty layers.
        used = set(cm.mapping.values())
        for i, h in enumerate(header):
            n = normalize_header(h) if h is not None else ""
            if n and i not in used and _RECOVERY.search(n):
                cm.mapping[f"recovery:{i}"] = i
        return cm


class ZenithArkAdapter(_ZenithAdapter):
    broker = "ARK"
    status_note = "ZENITH/ARK: quarterly 2nd surplus / local treaty returns; unverified"


class ZenithJordansAdapter(_ZenithAdapter):
    broker = "JORDANS"
    status_note = "ZENITH/JORDANS GLOBAL: quarterly foreign / 1st surplus treaty returns; unverified"
