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
from src.domain.cre_cleaner.adapters.mutual_benefits_ark import MutualBenefitsArkAdapter
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
    settings = {"tsi_gp_basis": "our_share", "uw_year_from_start_date": False}

    def claims_exclude(self, field: str, norm: str) -> bool:
        # Outstanding tab: 'Period Balance' is a reserve movement, not the
        # insurance period (Start Date / End Date are the period).
        return field == "period" and norm.startswith("PERIOD BALANCE")

    def map_claims_columns(self, header: Sequence[Any], aliases: Dict[str, List[str]]) -> ColumnMap:
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
        norms = [normalize_header(h) if h is not None else "" for h in header]
        basis, basis_src = self.setting_source("tsi_gp_basis")
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


class UnitrustArkAdapter(MutualBenefitsArkAdapter):
    """Unitrust & ARK: shares the ARK broker's file *discovery* (month files +
    quarterly fallback) only; Unitrust band layout; no other cedant's
    mapping, band, class or tab rules."""

    cedant = "UNITRUST"
    broker = "ARK"
    # Same Unitrust band layout as Agric (band label = share %, SI and
    # premium in the next two blank columns). TSI / GP stay on the 100%
    # columns (setting default): 'Our share SI' is blank in these returns.
    premium_layout_rules = UNITRUST_AGRIC_PREMIUM_RULES
    verified = False
    status_note = "Unitrust & ARK: month-file discovery; not checked against Bisola gold"
