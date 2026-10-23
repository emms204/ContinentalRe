"""NSIA quarterly treaty bordereaux via JORDANS (2019–2022) and ARK (2023–).

One tab per class (FIRE, ENG, BOND, AGRIC, CARGO, HULL) with a band row
(NET RETENTION | TREATY | FAC OBLIG) over repeated 'SUM INSURED | PREMIUM | %'
blocks, plus claims tabs and a treaty SUMMARY (statement only, not read as
rows). FAC OBLIG is the obligatory facultative treaty: the SUMMARY accounts
for it as a treaty block, so it is a treaty layer, never facultative.
"""
from __future__ import annotations

from pathlib import Path

from src.domain.cre_cleaner.adapters.source_layouts import SourceLayoutAdapter
from src.domain.cre_cleaner.adapters.workbook_rules import ControlledQuarterFiles, retype_tables, terrorism_tab
from src.domain.cre_cleaner.core.class_labels import is_unresolved_class, normalize_class_label
from src.domain.cre_cleaner.core.map_columns import is_share_header
from src.domain.cre_cleaner.core.normalize import clean_text, normalize_header, parse_number
from src.domain.cre_cleaner.models import ExceptionRecord

_BANDS = ("NET RETENTION", "TREATY", "OBLIGATORY TREATY")
_SKIP_TABS = {"ALL RAW", "OCT DEC 2022", "PIVOT", "YTD FINCON"}
_SI = {"SUM INSURED", "SUMINSURED"}
_PREM = {"PREMIUM", "GROSS PREMIUM"}
_PAID_COLS = {"TOTALCURRENTCLAIMPAID", "CLAIMS PAID", "TOTALAMOUNT", "CLAIMPAID", "PAYMENTDATE"}
_OST_COLS = {"OUTSTANDINGAMT", "TOTALRESERVE"}
_FIRE_TREATY_PACKAGE = {"PACKAGE", "PACKAGE POLICY", "GENERAL ACCIDENT"}


def allocation_triplets(header):
    """Start columns of 'SUM INSURED | PREMIUM | %' blocks. 2024 Q3 writes
    them 'SumInsured | Premium | prop' (BOND: '| Gross Premium |')."""
    n = [normalize_header(v) for v in header]
    return [i for i in range(len(n) - 2)
            if n[i] in _SI and n[i + 1] in _PREM and (is_share_header(n[i + 2]) or n[i + 2] == "PROP")]


def _has_band(row) -> bool:
    return any(any(w in normalize_header(v) for w in ("RETENTION", "TREATY", "FAC")) for v in row or [])


def _nsia_table_rule(header, rows, table):
    """ARK claims tabs carry 'Amount Paid' and an 'Outstanding Amount' that is
    zero on every row: paid claims. JORDANS paid tabs are named by their
    paid-total or payment-date column, outstanding tabs by OutstandingAmt."""
    from src.domain.cre_cleaner.core.table_type import OUTSTANDING, PAID, UNKNOWN
    h = set(header)
    if table.label != PAID and "AMOUNT PAID" in h:
        pi = header.index("AMOUNT PAID")
        oi = header.index("OUTSTANDING AMOUNT") if "OUTSTANDING AMOUNT" in h else None
        data = rows[table.header_row + 1:table.end_row]
        paid = any(parse_number(r[pi]) not in (None, 0) for r in data if pi < len(r))
        ost = oi is not None and any(parse_number(r[oi]) not in (None, 0) for r in data if oi < len(r))
        if paid and not ost:
            return PAID, "Amount Paid carries money; Outstanding Amount is zero or absent on every row"
    if table.label == UNKNOWN and h & _PAID_COLS and not h & _OST_COLS:
        return PAID, "paid-claims column " + ", ".join(sorted(h & _PAID_COLS))
    if table.label == UNKNOWN and h & _OST_COLS and not h & _PAID_COLS:
        return OUTSTANDING, "outstanding column " + ", ".join(sorted(h & _OST_COLS))
    return None


_CLAIMS_PPN_LABELS = {
    "ret": {"RETENTION %"},
    "treaty": {"TREATY %", "TREATY PPN", "TREATYPPN", "% TREATY", "%TREATYRECOVERIES",
               "PROP TREATY RECOVERY %", "SURPLUS %"},
    "fac": {"FAC %", "FAC PPN", "% FAC"},
}


def _shares_then_amounts(rows, i) -> bool:
    """Column i holds 0–1 shares and column i + 1 money on the block's rows."""
    pairs = [(parse_number(r[i]), parse_number(r[i + 1])) for r in rows if i + 1 < len(r)]
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None and (a or b)]
    return bool(pairs) and all(0 <= a <= 1 and abs(b) > 1 for a, b in pairs)


def _restated_header(header, rows):
    """Index in rows of a header row restated before any data (same labels
    plus added columns, no numbers), or None."""
    labels = {normalize_header(v) for v in header if clean_text(v)}
    for k, row in enumerate(rows):
        filled = [v for v in row if clean_text(v)]
        if not filled:
            continue
        if any(parse_number(v) is not None for v in filled):
            return None
        norms = {normalize_header(v) for v in filled}
        if len(labels) >= 6 and len(norms) > len(labels) and len(labels - norms) <= 1:
            return k
        return None
    return None


class _NsiaAdapter(ControlledQuarterFiles, SourceLayoutAdapter):
    verified = False
    status_note = "NSIA: source-layout mapping; not checked against approved gold workbooks"
    class_label_extra = {
        "ENGR": "Engineering",
        "AGR": "Agriculture",
        "FIRE (OBLIG)": "Fire",
        "FIRE OBLIG": "Fire",
    }
    premium_alias_extra = {
        # SubClass carries TERRORISM rows inside FIRE tabs (resolve_premium_class).
        "class": ["SUBCLASS", "SUB CLASS"],
        "insured": ["INSURED NAME"],
        "period_to": ["EXPIRY DATE"],
        "sum_insured": ["SUMINSURED"],
    }
    claims_alias_extra = {
        "class": ["CLASS OF INSURANCE"],
        "insured": ["INSUREDNAME", "PARTYNAME"],
        "policy_no": ["POLICYNO", "POLICY NUMBER"],
        "claim_no": ["CLAIMNO", "CLAIM NUMBER"],
        "date_of_loss": ["LOSSDATE", "LOSS DATE"],
        "period_from": ["STARTDATE"],
        "period_to": ["ENDDATE"],
        "details": ["DESCRIPTION OF LOSS", "LOSSDETAILS"],
        "ppn_ret": ["RETENTION %"],
        "amount_ret": ["RETENTION AMOUNT"],
        "ppn_treaty": ["TREATY %", "TREATY PPN", "TREATYPPN", "% TREATY", "%TREATYRECOVERIES",
                       "PROP TREATY RECOVERY %", "SURPLUS %"],
        "amount_treaty": ["TREATYRECOVERY", "TREATY RECOVERY", "TREATY AMOUNT", "TREATY AMT",
                          "PROP TREATY RECOVERY", "SURPLUS RECOVERY", "TREATYRECOVERIES",
                          "TREATY AMOUNT RECOVERIES", "FAC OBLIG RECOVERABLE"],
        "ppn_fac": ["FAC %", "FAC PPN", "% FAC"],
        "amount_fac": ["FAC RECOVERY", "FACRECOVERY", "FAC AMOUNT", "FACRECOVERIES"],
    }
    claims_total_fallback = {
        "paid": ("AMOUNT PAID", "TOTALCURRENTCLAIMPAID", "CLAIMS PAID", "CLAIMPAID", "TOTALAMOUNT"),
        "outstanding": ("OUTSTANDINGAMT", "OUTSTANDING AMOUNT"),
    }

    # --- columns ---------------------------------------------------------
    def claims_exclude(self, field, norm):
        # FAC OBLIG is an obligatory treaty layer; TOTAL… RECOVERABLE columns
        # add the recoveries up and are never the gross claim.
        if field == "amount_fac" and norm.startswith("FAC OBLIG"):
            return True
        if field in {"total_claims", "amount_treaty"} and norm.startswith("TOTAL") and "RECOVER" in norm:
            return True
        return super().claims_exclude(field, norm)

    def claims_header_cells(self, header, band_row, data_rows=(), notes=None):
        data_rows = list(data_rows)
        restated = _restated_header(header, data_rows)
        if restated is not None:
            # JORDANS 2021 Q1 ENGINEERING CLAIM: a header with no rows, then the
            # header restated with a Risk column added; the rows follow that one.
            header, band_row, data_rows = data_rows[restated], None, data_rows[restated + 1:]
            if notes is not None:
                notes.append(("INFO", "claims_header_restated",
                              "Header restated before the first row (columns added); rows read with the restated header"))
        cells = super().claims_header_cells(header, band_row, data_rows=data_rows, notes=notes)
        norms = [normalize_header(v) for v in cells]
        # JORDANS 2021 Q3 ENG: band row 'RETENTION SHARE | TREATY SHARE' over
        # 'RETENTION AMOUNT | TREATY PPN | TREATY PPN | TREATY AMOUNT'; the
        # values are share, amount, share, amount.
        for i in range(len(norms) - 3):
            if (norms[i:i + 4] == ["RETENTION AMOUNT", "TREATY PPN", "TREATY PPN", "TREATY AMOUNT"]
                    and _shares_then_amounts(data_rows, i) and _shares_then_amounts(data_rows, i + 2)):
                cells[i:i + 4] = ["RETENTION %", "RETENTION AMOUNT", "TREATY %", "TREATY AMOUNT"]
                norms = [normalize_header(v) for v in cells]
                if notes is not None:
                    notes.append(("WARN", "claims_header_labels_swapped",
                                  "Header 'RETENTION AMOUNT | TREATY PPN | TREATY PPN | TREATY AMOUNT' sits over "
                                  "share, amount, share, amount; read as RETENTION % | RETENTION AMOUNT | "
                                  "TREATY % | TREATY AMOUNT"))
                break
        # ARK 2023 Q2 repeated block headers read 'FacRecovery | treaty %'
        # over the treaty % and the treaty recovery (the first block reads
        # 'TREATY % | TreatyRecovery'). Relabel only when the values agree.
        if "TREATYRECOVERY" in norms or "TREATY RECOVERY" in norms:
            return cells
        for i in range(len(norms) - 1):
            if norms[i] == "FACRECOVERY" and norms[i + 1] == "TREATY %" and _shares_then_amounts(data_rows, i):
                cells[i], cells[i + 1] = "TREATY %", "TreatyRecovery"
                if notes is not None:
                    notes.append(("WARN", "claims_header_labels_swapped",
                                  "Header 'FacRecovery | treaty %' sits over the treaty % (0–1 shares) and the "
                                  "treaty recovery amount; read as TREATY % | TreatyRecovery"))
                break
        return cells

    def map_claims_columns(self, header, aliases, sheet_type=""):
        cm = super().map_claims_columns(header, aliases, sheet_type)
        norms = [normalize_header(v) for v in header]
        for field, label in (("risk", "RISK"), ("subrisk", "SUBRISK")):
            if label in norms:
                cm.mapping[field] = norms.index(label)
        # The shared rule takes the share column after each amount as its PPN:
        # after SURPLUS RECOVERY that is FAC OBLIG %, after FAC RECOVERY it is
        # PROP TREATY RECOVERY %. A column named for the band wins; FAC OBLIG
        # (a separate treaty layer, kept out) is never a band's PPN.
        for kind, labels in _CLAIMS_PPN_LABELS.items():
            named = next((i for i, n in enumerate(norms) if n in labels), None)
            current = cm.get("ppn_" + kind)
            if named is not None:
                cm.mapping["ppn_" + kind] = named
            elif current is not None and (norms[current].startswith("FAC OBLIG") or any(
                    norms[current] in other for k, other in _CLAIMS_PPN_LABELS.items() if k != kind)):
                del cm.mapping["ppn_" + kind]
        return cm

    def premium_header_cells(self, raw_rows, header_at, header, group_row):
        header = list(header)
        trip = allocation_triplets(header)
        for i in trip:
            header[i], header[i + 1], header[i + 2] = "SUM INSURED", "PREMIUM", "%"
        group = list(group_row or [])
        if trip and not _has_band(group):
            # No band row (ARK 2023 Q4 FIRE, 2025): every banded NSIA tab
            # names the blocks NET RETENTION, TREATY, FAC OBLIG in this order.
            group += [None] * max(0, len(header) - len(group))
            for k, i in enumerate(trip[:3]):
                group[i] = _BANDS[k]
        else:
            group = ["OBLIGATORY TREATY" if normalize_header(v).startswith("FAC O") else v for v in group]
        return super().premium_header_cells(raw_rows, header_at, header, group)

    # --- tabs ------------------------------------------------------------
    def classify_source_tab(self, file, sheet, rows):
        return retype_tables(file, sheet, rows, self.table_vocab, _nsia_table_rule)

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        rows = self._class_year_banners(sheet, rows, path, exceptions)
        if normalize_header(sheet) in _SKIP_TABS:
            exceptions.append(ExceptionRecord(
                "INFO", "non_bordereau_tab_skipped", path.name, sheet, 0,
                "Raw production extract / pivot / ledger tab: not ceded transaction rows"))
            return []
        for i, r in enumerate(rows[:20]):
            norms = {normalize_header(v) for v in r}
            if "DEDUCTIBLE" in norms and any("LAYER" in n for n in norms):
                exceptions.append(ExceptionRecord(
                    "WARN", "xol_tab_not_read", path.name, sheet, i + 1,
                    "Excess-of-loss layer schedule (DEDUCTIBLE / layers), not a proportional cession; "
                    "not read into the cleaned workbook"))
                return []
        for i, r in enumerate(rows):
            trip = allocation_triplets(r)
            if trip and not _has_band(rows[i - 1] if i else None) and not _has_band(rows[i - 2] if i > 1 else None):
                exceptions.append(ExceptionRecord(
                    "INFO", "allocation_bands_by_position", path.name, sheet, i + 1,
                    "No band row above the allocation blocks; assigned "
                    f"{', '.join(_BANDS[:len(trip)])} by position (the order of every banded NSIA tab)"))
        return rows

    def _class_year_banners(self, sheet, rows, path, exceptions):
        """'MARINE CARGO | 2019' banner rows above a claims block: the year
        cell keeps the class banner from being recognised. The class cell is
        kept alone; the U/W year stays in the rows' own UNDER YR column."""
        out = None
        for i, r in enumerate(rows):
            cells = [(j, v) for j, v in enumerate(r) if clean_text(v) if v is not None]
            if len(cells) != 2:
                continue
            (j0, a), (_j1, b) = cells
            yr = parse_number(b)
            if isinstance(a, str) and normalize_class_label(a, self.class_map()) not in {"", "Other"} \
                    and isinstance(yr, (int, float)) and 1990 <= yr <= 2100 and yr == int(yr):
                out = out or [list(x) for x in rows]
                out[i] = [None] * len(r)
                out[i][j0] = a
        if out is not None:
            exceptions.append(ExceptionRecord(
                "INFO", "class_year_banner_read", path.name, sheet, 0,
                "Class banners written with a year ('MARINE CARGO | 2019') read as class banners"))
        return out or rows

    # --- classes ---------------------------------------------------------
    def sheet_class_hint(self, sheet, class_hint):
        # Manual: PVT / Terrorism is its own class, never merged with Fire.
        return "TERRORISM" if terrorism_tab(sheet) else class_hint

    def resolve_premium_class(self, *, sheet_hint, section, raw, **context):
        if normalize_class_label(raw, self.class_map()) == "Terrorism & PVT":
            self._once(context, "INFO", "terrorism_separated_from_fire", raw,
                       f"Rows with subclass {raw!r} placed on Terrorism & PVT, not the {sheet_hint!r} tab's "
                       "class (Manual: PVT/Terrorism is never merged with Fire)")
            return raw, "column", raw
        if section:
            return section, "banner", section
        return sheet_hint, "sheet", context.get("sheet") or sheet_hint

    def resolve_claims_class(self, *, row, cmap, sheet_hint, section, **context):
        def value(field):
            i = cmap.get(field)
            return clean_text(row[i]) if i is not None and i < len(row) else ""
        parent, risk, subrisk = value("class"), value("risk"), value("subrisk")
        if parent and not any(ch.isalpha() for ch in parent):
            # ARK 2023 Q2 CARGO block: 'Class of Insurance' holds 40 / 20.
            # A number is never a class: the section banner decides.
            self._once(context, "WARN", "class_value_not_a_class", parent,
                       f"'Class of Insurance' value {parent!r} is not a class; "
                       f"the section {section or sheet_hint!r} is used")
            parent = ""
        cm = self.class_map()
        chosen = parent or section or sheet_hint
        if is_unresolved_class(chosen) or normalize_class_label(chosen, cm) in {"", "Other"}:
            # Bare MARINE (class, banner or tab): the row's own SubRisk / Risk
            # names Marine Cargo or Marine Hull. Never guessed from the claim
            # or policy number.
            for detail in (subrisk, risk):
                if normalize_class_label(detail, cm) in {"Marine Cargo", "Marine Hull"}:
                    return detail, "column", f"{chosen} / {detail}"
        if normalize_class_label(risk, cm) == "Terrorism & PVT":
            return risk, "column", risk
        if parent and normalize_header(parent) in _FIRE_TREATY_PACKAGE:
            self._once(context, "WARN", "class_grouping_differs", parent,
                       f"Claims with class {parent!r} (Goods-In-Transit, All Risk, liability, money …) are "
                       "ceded in the FIRE premium tabs and recovered under the FIRE treaty in the cedant's "
                       "SUMMARY (2024 Q4: FIRE claims recoveries = Fire + General Accident rows); placed on Fire")
            return "Fire", "column", parent
        if parent:
            return parent, "column", parent
        if section:
            return section, "banner", section
        return sheet_hint, "sheet", context.get("sheet") or sheet_hint

    def _once(self, context, severity, code, raw, detail):
        seen = self.__dict__.setdefault("_class_notes", set())
        path = context.get("path")
        key = (code, path.name if path else "", context.get("sheet"), raw)
        if key in seen:
            return
        seen.add(key)
        context["exceptions"].append(ExceptionRecord(
            severity, code, key[1], context.get("sheet") or "", context.get("excel_row") or 0, detail))


class NsiaJordansAdapter(_NsiaAdapter):
    cedant = "NSIA"
    broker = "JORDANS"
    superseded_files = {
        # Same rows; the plain copy has only one corrected hull total cell.
        (2020, 1): {
            "2020 FIRST QUARTER PREMIUM BORDEREAU.xls":
                "'… - to Reins Marine Hull Revised.xls' is the expressly revised copy",
            "2020 FIRST QUARTER PREMIUM BORDEREAU - to Reins.xls":
                "'… - to Reins Marine Hull Revised.xls' is the expressly revised copy",
            # Data cells identical to '2020 FIRST QUARTER CLAIMS PAID.xlsx'.
            "2020 FIRST QUARTER CLAIMS PAID...xlsx":
                "same paid claims as '2020 FIRST QUARTER CLAIMS PAID.xlsx' (one blank cell differs)",
        },
        (2022, 2): {"Treaty Borx Q2 2022 FINAL.xls":
                    "its AGRIC and ENG tabs replace those tabs of 'NSIA Treaty Bordereaux Q2 2022.xls'"},
    }
    additive_quarters = {
        (2022, 1): "general treaty review and FAC-obligatory treaty are separate cessions",
    }
    # 2022 Q2: the FINAL copy covers AGRIC and ENG only; its tabs control.
    tab_overlays = {
        "NSIA Treaty Bordereaux Q2 2022.xls": ("Treaty Borx Q2 2022 FINAL.xls", ("AGRIC", "ENG")),
    }

    def read_source_workbook(self, path):
        sheets, notes = super().read_source_workbook(path)
        overlay = next((v for k, v in self.tab_overlays.items() if k.casefold() == Path(path).name.casefold()), None)
        final = Path(path).with_name(overlay[0]) if overlay else None
        if final is None or not final.exists():
            return sheets, notes
        final_sheets, final_notes = super().read_source_workbook(final)
        for tab in overlay[1]:
            src = next((s for s in final_sheets if normalize_header(s) == tab), None)
            dst = next((s for s in sheets if normalize_header(s) == tab), None)
            if src and dst:
                sheets[dst] = final_sheets[src]
                notes.append(("INFO", "final_tab_overlay", dst, 0,
                              f"Tab {dst!r} read from {final.name!r} (final copy of this tab)"))
        notes.extend(n for n in final_notes if n[2] in overlay[1])
        return sheets, notes


class NsiaArkAdapter(_NsiaAdapter):
    cedant = "NSIA"
    broker = "ARK"
    superseded_files = {
        (2024, 4): {
            "4TH QUARTER Treaty Borx 2024.xls": "'4TH QUARTER 3 F.xls' is the final copy (F): rebuilt claims tab, "
                                                "ENG SUMMARY split by U/W year",
            "4TH QUARTER Treaty Borx 2024..xls": "'4TH QUARTER 3 F.xls' is the final copy (F)",
        },
    }
    additive_quarters = {
        (2024, 2): "'GEN' (general treaty) and 'FOBLIG' (FAC-obligatory treaty) are separate cessions",
    }
