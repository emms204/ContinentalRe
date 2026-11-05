"""Unitrust adapters (Agric; Unitrust & ARK) — Unitrust rules only.

IMPL-20260929-04 moved these out of the shared core. Evidence: 2ND QTR 2024
AGRIC TREATY.xlsx 'AGRIC  NAIRA' (header row 1):

  J Sum Insured | K Our share SI | L Gross Premium | M (blank) |
  N Our Share of G. Prem. | O NET RETENTION (=30) | P (blank) | Q (blank) |
  R TREATY QUOTA SHARE (=70) | S (blank) | T (blank)

RETENTION %/SI/premium ← O/P/Q and TREATY (Quota Share) %/SI/premium ←
R/S/T: the band label *is* the share-% column; its SI and premium sit in the
next two blank-header columns.

TOTAL SUM INSURED / GROSS PREMIUM follow the adapter setting
``tsi_gp_basis`` (base.py settings; overridable per year / quarter in
``settings_by_period``). Unitrust default = ``"our_share"`` (Bisola,
2026-09-29 13:37 / 13:44 WAT): TSI <- 'Our share SI' (K), GP <- 'Our Share of
G. Prem.' (N). ``"100"``: TSI <- 'Sum Insured' (J), GP <- 'Gross Premium' (L).
Bisola's gold shows the 100% figures; those gold TSI / GP cells are known
gold errors. The columns used are logged per sheet (INFO ``tsi_gp_basis``)
and the setting in the source-audit notes; a missing column for the chosen
basis leaves TSI / GP blank with a WARN ``tsi_gp_column_missing`` (never a
silent switch to the other basis).

Verification only (never changes a value): each row's our-share GP is
checked against RET + TREATY (+ other layers) + FAC premium, and our-share
SI against the sum of the SI blocks, within max(1 unit, 0.5%); a miss is
WARN ``allocation_not_matching_base``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.adapters.mutual_benefits import MutualBenefitsArkAdapter
from src.domain.cre_cleaner.core.detect import SheetTypeRules
from src.domain.cre_cleaner.core.map_columns import ColumnMap, PremiumLayoutRules, map_simple_columns
from src.domain.cre_cleaner.core.normalize import normalize_header
from src.domain.cre_cleaner.models import ExceptionRecord

# Allocation verification tolerance: max(1 unit, 0.5 % of the base value).
ALLOCATION_TOL_ABS = 1.0
ALLOCATION_TOL_REL = 0.005

# Band-label headers that hold the share % (SI + premium in the next two
# blank-header columns).
UNITRUST_SHARE_PPN_HEADERS = frozenset({
    "NET RETENTION", "NET RETENTIONS", "TREATY QUOTA SHARE",
})


# Claims / outstanding bands: the band label column holds the PPN (e.g. 30),
# the next column the amount (paid: 'Net Retention' | <amount>; with a
# PPN / AMOUNT sub-row the merged names are '<label> PPN' / '<label> AMOUNT').
# Bisola's gold TREATY band = TREATY QUOTA SHARE; the other treaty layers
# (first surplus, XoL) are kept as extra layers.
UNITRUST_CLAIMS_BANDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("ret", ("NET RETENTION", "NET RETENTIONS")),
    ("treaty", ("TREATY QUOTA SHARE",)),
    ("fac", ("FACULTATIVE",)),
)
UNITRUST_CLAIMS_EXTRA_LAYERS = ("TREATY FIRST SURPLUS", "TREATY EXCESS OF LOSS LAYER 1")
UNITRUST_CLAIMS_ALIAS_EXTRA: Dict[str, List[str]] = {
    # Outstanding tab: 'Outstanding Amount' is the loss reserve (gold).
    "total_claims": ["OUTSTANDING AMOUNT"],
}


def _band_cols(norms: List[str], labels: Sequence[str]) -> Tuple[Optional[int], Optional[int]]:
    """(ppn_col, amount_col) of a claims band labelled by one of ``labels``."""
    for i, n in enumerate(norms):
        for lab in labels:
            if n == f"{lab} PPN":
                amt = next((j for j, m in enumerate(norms) if m == f"{lab} AMOUNT"), None)
                return i, amt
            if n == lab:
                nxt = norms[i + 1] if i + 1 < len(norms) else None
                if nxt in ("", "AMOUNT"):
                    return i, i + 1
                # A plain 'Net Retention' amount column (no PPN|amount pair)
                # is not a Unitrust band: leave it to the alias mapping.
    return None, None


def _our_share_kind(norm: str) -> str:
    """'si' / 'gp' for an our-share SI / premium header, else ''."""
    if not is_our_share_header(norm):
        return ""
    rest = norm[len("OUR SHARE"):]
    if "PREM" in rest:
        return "gp"
    if " SI" in f" {rest}" or "SUM INSURED" in rest:
        return "si"
    return ""


def is_our_share_header(norm: str) -> bool:
    """Cedant co-insurance share of SI / premium ('Our share SI', 'Our Share
    of G. Prem.') — never the policy (100%) TSI or gross premium."""
    return bool(norm) and norm.startswith("OUR SHARE")


def apply_tsi_gp_basis(adapter: Any, cm: ColumnMap, header: Sequence[Any], *,
                       path: Optional[Path] = None, sheet: str = "",
                       exceptions: Optional[list] = None) -> ColumnMap:
    """TSI / GP columns for the ``tsi_gp_basis`` setting ("our_share": 'Our
    share SI' / 'Our Share of G. Prem.'; "100": the engine's 100% columns),
    logged per sheet. A missing column is left blank with a WARN."""
    norms = [normalize_header(h) if h is not None else "" for h in header]
    basis, basis_src = adapter.setting_source("tsi_gp_basis")
    used = {}
    for field, kind in (("sum_insured", "si"), ("gross_premium", "gp")):
        if basis == "our_share":
            pick = next((i for i, n in enumerate(norms) if _our_share_kind(n) == kind), None)
        else:
            pick = cm.mapping.get(field)
        if pick is None:
            cm.mapping.pop(field, None)
        else:
            cm.mapping[field] = pick
        used[field] = pick
    if exceptions is not None and path is not None:
        desc = ", ".join(
            f"{'TSI' if f == 'sum_insured' else 'GP'} <- "
            + ("(none)" if i is None else f"col {i + 1} {norms[i]!r}")
            for f, i in used.items()
        )
        rec = ExceptionRecord("INFO", "tsi_gp_basis", path.name, sheet, 0,
                              f"TSI/GP basis={basis} ({basis_src}): {desc}")
        if not any(e.reason == rec.reason and e.source_sheet == rec.source_sheet and e.detail == rec.detail
                   and e.source_filename == rec.source_filename for e in exceptions):
            exceptions.append(rec)
        missing = [f for f, i in used.items() if i is None]
        if missing:
            exceptions.append(ExceptionRecord(
                "WARN", "tsi_gp_column_missing", path.name, sheet, 0,
                f"basis={basis}: no column for {missing}; left blank (other basis not substituted)",
            ))
    return cm


class UnitrustAgricPremiumRules(PremiumLayoutRules):
    """Generic vocabulary + Unitrust Agric share-label bands."""

    def is_ppn_header(self, norm: str) -> bool:
        return norm in UNITRUST_SHARE_PPN_HEADERS or super().is_ppn_header(norm)

    def block_for_ppn(
        self, pi: int, norms: List[str], si_idxs: List[int], prem_idxs: List[int],
    ) -> Optional[Tuple[int, Optional[int], Optional[int]]]:
        # NET RETENTION | <blank SI> | <blank premium>: the triple is positional
        # and must win over a nearby 'Our Share of G. Prem.' column.
        if (
            norms[pi] in UNITRUST_SHARE_PPN_HEADERS
            and pi + 2 < len(norms)
            and not norms[pi + 1]
            and not norms[pi + 2]
        ):
            return (pi, pi + 1, pi + 2)
        return super().block_for_ppn(
            pi, norms, [i for i in si_idxs if not is_our_share_header(norms[i])],
            [i for i in prem_idxs if not is_our_share_header(norms[i])],
        )


UNITRUST_AGRIC_PREMIUM_RULES = UnitrustAgricPremiumRules()


# Unitrust & ARK 2023–2025 premium: every band label is a share-% column with
# its SI and premium in the next two blank columns:
#   NET RETENTION | TREATY FIRST SURPLUS | TREATY QUOTA SHARE | Facultative
# First surplus is the TREATY band; quota share is the TREATY band on a sheet
# without first surplus and a second treaty layer otherwise (the engine's
# extra_treaty_blocks, WARN multiple_treaty_layers).
UNITRUST_ARK_SHARE_PPN_HEADERS = UNITRUST_SHARE_PPN_HEADERS | {"TREATY FIRST SURPLUS", "FACULTATIVE"}


class UnitrustArkPremiumRules(UnitrustAgricPremiumRules):
    """Agric's share-label triples plus the ARK first surplus and FAC bands."""

    def is_ppn_header(self, norm: str) -> bool:
        return norm in UNITRUST_ARK_SHARE_PPN_HEADERS or super().is_ppn_header(norm)

    def block_for_ppn(
        self, pi: int, norms: List[str], si_idxs: List[int], prem_idxs: List[int],
    ) -> Optional[Tuple[int, Optional[int], Optional[int]]]:
        if (
            norms[pi] in UNITRUST_ARK_SHARE_PPN_HEADERS
            and pi + 2 < len(norms)
            and not norms[pi + 1]
            and not norms[pi + 2]
        ):
            return (pi, pi + 1, pi + 2)
        return super().block_for_ppn(pi, norms, si_idxs, prem_idxs)


UNITRUST_ARK_PREMIUM_RULES = UnitrustArkPremiumRules()


class UnitrustAgricAdapter(QuarterlyWorkbookAdapter):
    cedant = "UNITRUST"
    broker = "AGRIC"
    verified = False
    status_note = "Unitrust Agric: quarterly returns; unverified"

    premium_layout_rules = UNITRUST_AGRIC_PREMIUM_RULES
    claims_alias_extra = UNITRUST_CLAIMS_ALIAS_EXTRA
    # Band label row + sub-row (2023 claims: PPN / AMOUNT; 2025 premium:
    # Sum Insured / Gross Premium / %) — merged into one header.
    band_subrow_layout = True
    # Explicit settings (base.py); per-period overrides go in settings_by_period.
    settings = {
        "tsi_gp_basis": "our_share",
        "uw_year_from_start_date": False,
        "single_class": "Agriculture",
    }

    def claims_exclude(self, field: str, norm: str) -> bool:
        # Outstanding tab: 'Period Balance' is a reserve movement, not the
        # insurance period (Start Date / End Date are the period).
        return field == "period" and norm.startswith("PERIOD BALANCE")

    def map_claims_columns(
        self, header: Sequence[Any], aliases: Dict[str, List[str]], sheet_type: str = "",
    ) -> ColumnMap:
        norms = [normalize_header(h) if h is not None else "" for h in header]
        band_cols = set()
        bands = {}
        for key, labels in UNITRUST_CLAIMS_BANDS:
            ppn, amt = _band_cols(norms, labels)
            bands[key] = (ppn, amt)
            band_cols.update(c for c in (ppn, amt) if c is not None)
        layers = []
        layer_cols = {}
        for n, lab in enumerate(UNITRUST_CLAIMS_EXTRA_LAYERS, start=1):
            ppn, amt = _band_cols(norms, (lab,))
            band_cols.update(c for c in (ppn, amt) if c is not None)
            if amt is not None:
                layers.append((amt, lab))
            for part, c in (("ppn", ppn), ("amount", amt)):
                if c is not None:
                    layer_cols[f"layer{n}_{part}"] = c
        # Band columns are positional: keep them away from the alias mapping.
        masked = [None if i in band_cols else h for i, h in enumerate(header)]
        cm = map_simple_columns(masked, aliases, exclude=self.claims_exclude)
        for key, (ppn, amt) in bands.items():
            if ppn is None and amt is None:
                continue  # no Unitrust band here: alias mapping stands
            cm.mapping.pop(f"amount_{key}", None)
            if ppn is not None:
                cm.mapping[f"ppn_{key}"] = ppn
            if amt is not None:
                cm.mapping[f"amount_{key}"] = amt
        # Layer columns are recorded so they are not re-found by alias.
        cm.mapping.update(layer_cols)
        cm.extra_amount_columns = layers
        return cm

    def map_premium_columns(
        self,
        header: Sequence[Any],
        group_row: Optional[Sequence[Any]],
        *,
        path: Optional[Path] = None,
        sheet: str = "",
        exceptions: Optional[list] = None,
    ) -> ColumnMap:
        # Bands and 100% TSI / GP from the shared engine (our-share / band
        # headers vetoed); the tsi_gp_basis setting then picks the columns.
        cm = self.detect_premium_allocation_blocks(header, group_row)
        return apply_tsi_gp_basis(self, cm, header, path=path, sheet=sheet, exceptions=exceptions)

    def check_premium_row(self, prow: Any, *, exceptions: list, path: Any, sheet: str,
                          excel_row: int) -> None:
        """Verification flag only: TSI / GP (chosen basis) vs the band sums."""
        basis = self.setting("tsi_gp_basis")
        layers = list(getattr(prow, "extra_layers", None) or [])
        checks = (
            ("GP", prow.gross_premium,
             [prow.ret_prem, prow.sur_prem, prow.fac_prem] + [l.get("prem") for l in layers]),
            ("TSI", prow.total_sum_insured,
             [prow.ret_si, prow.sur_si, prow.fac_si] + [l.get("si") for l in layers]),
        )
        for label, base, parts in checks:
            vals = [p for p in parts if p is not None]
            if base is None and not vals:
                continue
            total = sum(vals)
            tol = max(ALLOCATION_TOL_ABS, ALLOCATION_TOL_REL * abs(base or 0.0))
            if base is None or not vals or abs(base - total) > tol:
                exceptions.append(ExceptionRecord(
                    "WARN", "allocation_not_matching_base", getattr(path, "name", str(path)),
                    sheet, excel_row,
                    f"{label} {basis}={base} vs RET+TREATY+FAC{' +layers' if layers else ''}"
                    f"={total if vals else None} (tol {tol:g}); value kept from the basis column",
                ))

    def premium_exclude(self, field: str, norm: str) -> bool:
        # Our-share columns are never TSI or GP (Manual / Bisola gold), and a
        # band's own SI / premium (RETENTION …, TREATY …) is never the gross.
        return field in {"sum_insured", "gross_premium"} and (
            is_our_share_header(norm) or bool(self.premium_layout_rules.band_kind(norm))
        )


# 2019–2020 'REINSURANCE PROPORTIONAL DISTRIBUTION REPORT' claims exports:
#   Amount of claim Paid | % | Company Retention Payment | % | Quotas Share
#   Payment | % | First Surplus Payment | % | Second Surplus Payment | % |
#   Facultative Payment   (outstanding: '… O/S' instead of '… Payment')
# Each '%' is the share of the band to its right. Older files split the
# labels over two rows ('Amount of' / 'claim Paid', 'Effective' / 'Date').
# Quota share is the TREATY band; first / second surplus are extra layers.
# Total-claims labels are exact: any other 'AMOUNT OF …' header (salvage,
# premium, recovery) is never the total.
UNITRUST_ARK_TOTAL_CLAIMS = ("AMOUNT OF CLAIM PAID", "AMOUNT OF CLAIM O S", "AMOUNT OF CLAIM", "AMOUNT OF")
UNITRUST_ARK_CLAIMS_ALIAS_EXTRA: Dict[str, List[str]] = {
    "total_claims": ["OUTSTANDING AMOUNT", *UNITRUST_ARK_TOTAL_CLAIMS],
    "amount_ret": ["COMPANY RETENTION"],
    "amount_treaty": ["QUOTAS SHARE", "QUOTA SHARE"],
    "date_of_loss": ["LOSSDATE"],
    "period_from": ["EFFECTIVE DATE", "EFFECTIVE"],
    "period_to": ["EXPIRYDATE", "EXPIRY DATE"],
}


_ARK_CLAIM_BANDS = {
    'ret': ('COMPANY RETENTION', 'NET RETENTION', 'NET RETENTIONS'),
    'quota': ('QUOTAS SHARE', 'QUOTA SHARE', 'TREATY QUOTA SHARE'),
    'surplus1': ('FIRST SURPLUS', 'TREATY FIRST SURPLUS'),
    'surplus2': ('SECOND SURPLUS', 'TREATY SECOND SURPLUS'),
    'fac': ('FACULTATIVE',),
}
_ARK_BAND_NAMES = {key: names[0] for key, names in _ARK_CLAIM_BANDS.items()}


def _ark_claim_band(norm):
    """Exact source band/part; generic AMOUNT and unknown tail pairs stay out."""
    for key, names in _ARK_CLAIM_BANDS.items():
        for name in names:
            for suffix, part in ((' PPN', 'ppn'), (' AMOUNT', 'amount'),
                                 (' PAYMENT', 'amount'), (' O S', 'amount'), ('', '')):
                if norm == name + suffix:
                    return key, part
    return None, ''


class UnitrustArkAdapter(MutualBenefitsArkAdapter):
    """Unitrust & ARK: shares the ARK broker's file *discovery* (month files +
    quarterly fallback) only; Unitrust band layout; no other cedant's
    mapping, band, class or tab rules."""

    cedant = "UNITRUST"
    broker = "ARK"
    # Unitrust band layout (band label = share %, SI and premium in the next
    # two blank columns) with the ARK first surplus and FAC bands.
    premium_layout_rules = UNITRUST_ARK_PREMIUM_RULES
    claims_alias_extra = UNITRUST_ARK_CLAIMS_ALIAS_EXTRA
    # Prepare only source-named pairs ourselves. Generic forward filling of
    # a parent across an unlabelled third pair can invent another treaty band.
    band_subrow_layout = False
    # Mutual's claims class / PPN choices are not Unitrust's: defaults.
    # TSI / GP on Unitrust's share, the basis the bands are computed on
    # ('Our share SI' is filled on 99.5% of the 2023–2025 rows): as Agric.
    settings: dict = {"tsi_gp_basis": "our_share"}
    claims_sheet_class_first = False
    # Tabs are split by currency ('G.A NAIRA' / 'G.A DOLLAR', 'USD Marine'):
    # one class label per class, not one per currency.
    sheet_type_rules = SheetTypeRules(
        class_noise_words=frozenset({"NAIRA", "DOLLAR", "DOLLARS", "USD", "NGN"}),
    )
    verified = False
    status_note = "Unitrust & ARK: month-file discovery; not checked against Bisola gold"

    def premium_header_row(self, raw_rows, found):
        # Q3 2022: a 'PPN | SUM INSURED | PREMIUM' caption row sits under the
        # band names and outscores the real header ('SUM INSURED' reads as
        # INSURED). The header is the row above, with Policy No and Insured.
        if found < 1 or any(normalize_header(c).startswith("POLICY") for c in raw_rows[found] if c is not None):
            return found
        caps = {normalize_header(c) for c in raw_rows[found] if c is not None and normalize_header(c)}
        above = [normalize_header(c) for c in raw_rows[found - 1] if c is not None]
        if caps <= {"PPN", "%", "SUM INSURED", "SI", "PREMIUM"} and \
                any(n.startswith("POLICY") for n in above) and "INSURED" in above:
            return found - 1
        return found

    def map_premium_columns(self, header, group_row, *, path=None, sheet="", exceptions=None):
        cm = super().map_premium_columns(header, group_row, path=path, sheet=sheet, exceptions=exceptions)
        return apply_tsi_gp_basis(self, cm, header, path=path, sheet=sheet, exceptions=exceptions)

    def claims_exclude(self, field: str, norm: str) -> bool:
        return (field == "total_claims" and "AMOUNT OF" in norm
                and norm not in UNITRUST_ARK_TOTAL_CLAIMS
                or field == 'period' and norm.startswith('PERIOD BALANCE'))

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        from math import isclose
        from src.domain.cre_cleaner.core.normalize import parse_number

        self.__dict__.setdefault('_ark_source_tables', {})[(path.name, sheet)] = rows
        result = list(rows)
        for hi, row in enumerate(rows):
            norms = [normalize_header(v) for v in row]
            if not any(h in {'POLICY', 'POLICY NO', 'POLICY NUMBER'} for h in norms) or not any(
                    h in {'CLAIM NO', 'CLAIM NUMBER', 'CLAIMS NO'} for h in norms):
                continue
            starts = [(i, *_ark_claim_band(h)) for i, h in enumerate(norms) if _ark_claim_band(h)[0]]
            if not starts:
                continue
            header = list(row)
            below = rows[hi + 1] if hi + 1 < len(rows) else []
            sub = [normalize_header(v) for v in below]
            cm = map_simple_columns(row, self.claims_aliases(), exclude=self.claims_exclude)
            pi, ci, ti = cm.get('policy_no'), cm.get('claim_no'), cm.get('total_claims')
            end = next((j for j in range(hi + 1, len(rows)) if any(
                normalize_header(v) in {'POLICY', 'POLICY NO', 'POLICY NUMBER'} for v in rows[j])
                and any(normalize_header(v) in {'CLAIM NO', 'CLAIM NUMBER', 'CLAIMS NO'} for v in rows[j])), len(rows))
            data = [r for r in rows[hi + 1:end] if pi is not None and ci is not None
                    and max(pi, ci) < len(r) and r[pi] and r[ci]]
            def corroborated(i):
                for r in data:
                    if ti is None or max(i + 1, ti) >= len(r):
                        continue
                    share, amount, total = [parse_number(r[j]) for j in (i, i + 1, ti)]
                    if share is None or amount is None or total in (None, 0) or abs(share) > 100:
                        continue
                    if amount and any(isclose(amount, total * factor, rel_tol=1e-8,
                            abs_tol=max(.03, abs(total) * .0001)) for factor in (share, share / 100)):
                        return True
                return False

            proofs = {i: corroborated(i) for i, _, part in starts if not part
                      and i + 1 < len(norms) and not norms[i + 1]}
            owned = set()
            for i, key, part in starts:
                label = next(name for name in _ARK_CLAIM_BANDS[key]
                             if norms[i] == name or norms[i].startswith(name + ' '))
                if part:
                    header[i] = label + (' PPN' if part == 'ppn' else ' AMOUNT')
                    owned.add(i)
                    if part == 'amount' and i and norms[i - 1] == '%':
                        header[i - 1] = label + ' PPN'
                        owned.add(i - 1)
                    continue
                if i and norms[i - 1] == '%':
                    header[i - 1], header[i] = label + ' PPN', label + ' AMOUNT'
                    owned.update((i - 1, i))
                elif i + 1 < len(norms) and not norms[i + 1]:
                    explicit = (sub[i:i + 2] in (['PPN', 'AMOUNT'], ['%', 'AMOUNT']))
                    zero_pair = bool(data) and all(all(j >= len(r) or r[j] in (None, '')
                        or parse_number(r[j]) == 0 for j in (i, i + 1)) for r in data)
                    # A source-named all-zero band shares the proved pair
                    # schema; copy its zeroes rather than manufacturing blanks.
                    if explicit or proofs.get(i) or (any(proofs.values()) and zero_pair):
                        header[i:i + 2] = [label + ' PPN', label + ' AMOUNT']
                    else:
                        header[i:i + 2] = ['UNITRUST UNVERIFIED ALLOCATION'] * 2
                        exceptions.append(ExceptionRecord('WARN', 'unitrust_claims_band_ambiguous',
                            path.name, sheet, hi + 1, f'{row[i]!r} columns {i + 1}/{i + 2} have '
                            'no PPN/AMOUNT captions or corroborating source arithmetic. Both left unmapped.'))
                    owned.update((i, i + 1))
                else:
                    # An explicitly named standalone amount has no adjacent
                    # blank amount child or leading share column.
                    header[i] = label + ' AMOUNT'
                    owned.add(i)
            unknown = [i for i in range(min(i for i, _, _ in starts), len(norms))
                       if i not in owned and not norms[i] and any(i < len(r) and r[i] is not None for r in data)]
            if unknown:
                exceptions.append(ExceptionRecord('WARN', 'unitrust_unlabelled_claim_allocation', path.name,
                    sheet, hi + 1, f'Unlabelled allocation columns {[i + 1 for i in unknown]} retained in '
                    'the source only; their role is not assumed to be FAC or another treaty layer.'))
            # A duplicated explicit source band does not identify which pair
            # belongs in the one upload band. It is not silently combined.
            counts = {}
            for h in header:
                key, part = _ark_claim_band(normalize_header(h))
                if key and part:
                    counts[(key, part)] = counts.get((key, part), 0) + 1
            if any(n > 1 for n in counts.values()):
                exceptions.append(ExceptionRecord('WARN', 'unitrust_claims_band_ambiguous', path.name,
                    sheet, hi + 1, 'Repeated source allocation band captions; ambiguous bands left unmapped.'))
            result[hi] = header
        return result

    def classify_source_tab(self, file, sheet, rows):
        from src.domain.cre_cleaner.core.table_type import classify_tab
        # Header role annotations are for mapping only. Keep family/type and
        # confidence based on the original source captions and values.
        source = self.__dict__.get('_ark_source_tables', {}).get((file, sheet), rows)
        return classify_tab(file, sheet, source, vocab=self.table_vocab)

    def map_claims_columns(self, header, aliases, sheet_type=''):
        norms = [normalize_header(v) for v in header]
        bands = {}
        for i, h in enumerate(norms):
            key, part = _ark_claim_band(h)
            if key:
                bands.setdefault(key, {}).setdefault(part or 'amount', []).append(i)
        masked = [None if _ark_claim_band(h)[0] else header[i] for i, h in enumerate(norms)]
        cm = map_simple_columns(masked, aliases, exclude=self.claims_exclude)
        # Start/End Date are the authoritative cover columns when supplied.
        # A balance/movement or an invalid combined range must not erase them.
        if cm.get('period_from') is not None and cm.get('period_to') is not None:
            cm.mapping.pop('period', None)
        proven = {k: {part: cols[0] for part, cols in parts.items()}
                  for k, parts in bands.items() if all(len(cols) == 1 for cols in parts.values())}
        for band, field in [('ret', 'ret'), ('fac', 'fac')]:
            for part in ('ppn', 'amount'):
                if part in proven.get(band, {}):
                    cm.mapping[part + '_' + field] = proven[band][part]
        original = map_simple_columns(header, aliases, exclude=self.claims_exclude)
        selected = original.get('amount_treaty')
        primary = _ark_claim_band(norms[selected])[0] if selected is not None else None
        if 'amount' not in proven.get(primary, {}):
            primary = next((k for k in ('quota', 'surplus1', 'surplus2') if 'amount' in proven.get(k, {})), None)
        if primary:
            for part, col in proven[primary].items():
                cm.mapping[part + '_treaty'] = col
        cm.extra_amount_columns = [(parts['amount'], _ARK_BAND_NAMES[key])
            for key, parts in proven.items() if key in {'quota', 'surplus1', 'surplus2'}
            and key != primary and 'amount' in parts]
        return cm
