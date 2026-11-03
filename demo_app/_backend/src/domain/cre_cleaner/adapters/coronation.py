"""CORONATION Insurance (formerly Wapic) via SCIB and UAIB.

SCIB (2019–22): motor only, one workbook per quarter with premium, paid and
outstanding (OCR) tabs. UAIB (2020–25): multi-class treaty workbooks (premium
+ settled claims) and separate OCR (outstanding) snapshots "as at" a month end.
Brief: outputs/coronation-engineering-brief.md (approved 2026-10-28).

Layouts:
- Premium P1 (2019–21): CESSION | POLICY NO | … | GROSS SUM | % EML | EML SUM |
  GROSS | % | RETAIN | % | CEDED | % | LOCAL FAC, with INSURED / PREMIUM on the
  row below; each % sits before its amount.
- Premium P2 (Q4 2021–25): Gross Sum Insured | Gross Premium | Coronation SI |
  Coronation Premium | % | Treaty Limit | Treaty SI | Treaty Premium |
  Treaty Cession(%) | FAC%. Class banners (ENGINEERING U/Y 2024) group rows.
- Paid C1 (2019–20): Payout Amount Total then %/amount or Retention (%) /
  Retention (N) … pairs. Paid C2 (StarINS settled export, 2021–25): Payout
  Amount Total, Treaty Recoverable%/Amount, or Treaty %/Amount, FAC %/AMOUNT,
  NLIP %/AMOUNT (local pool, kept out of the bands).
- OCR: the amount column is named for its date (ADJUSTED DEC, 2022 OCR /
  OCR AS AT 31/12/2025); tabs carry STATUS and AGE ANALYSIS.

Decisions (brief §8, all option a): Motor subclasses map to Motor; NLIP and
co-insurance recoveries are kept out with a WARN; retention is left blank when
the source states none (no derivation); near-duplicate copies are chosen per
pair below; OCR snapshots map to the quarter of their as-at month.
"""
from __future__ import annotations

import re

from src.domain.cre_cleaner.adapters.source_layouts import SourceLayoutAdapter
from src.domain.cre_cleaner.adapters.workbook_rules import ControlledQuarterFiles, retype_tables
from src.domain.cre_cleaner.core.class_labels import is_unresolved_class, normalize_class_label
from src.domain.cre_cleaner.core.map_columns import is_share_header
from src.domain.cre_cleaner.core.normalize import clean_text, normalize_header, parse_number
from src.domain.cre_cleaner.models import ExceptionRecord

# OCR amount columns are named for the snapshot date.
_OCR_TOTAL = re.compile(r"^(ADJUSTED\b.*\bOCR\b.*|OCR AS AT\b.*)$")
_OCR_MARKERS = {"STATUS", "AGE ANALYSIS", "AGE ANALYSIS DAYS"}
# Report names used as tab names: never a class.
_REPORT_TAB = re.compile(r"\b(PREMIUM|BORD\w*|SETTLED|RECOVERABLE|REC|CLAIMS?|OCR|OUTSTA\w*|SHEET)\b")
_UY_BANNER = re.compile(r"^\d{4}\s*U\s*/?\s*Y$|^U\s*/?\s*W\s*\d{4}$")


_NAMED_PPN = {
    "ret": {"RETENTION %", "RETAINED %", "RETAINED PPN", "RETENTION PPN"},
    "treaty": {"TREATY %", "TREATY RECOVERABLE %", "% TREATY PPN", "TREATY PPN"},
    "fac": {"FAC %", "FAC RECOVERABLE %", "% FAC PPN", "FAC PPN"},
}


def _column_number_row(row) -> bool:
    """'1 | 2 | 2 | 4 | 5 …' printed over P1 headers: column numbers, not a policy."""
    nums = [parse_number(v) for v in row if clean_text(v)]
    return len(nums) >= 8 and all(n is not None and float(n).is_integer() and 0 < n <= 60 for n in nums) \
        and sum(1 for a, b in zip(nums, nums[1:]) if b >= a) >= len(nums) - 2


def _norms(header):
    return [normalize_header(v) if v is not None else "" for v in header]


def _ocr_total_index(norms):
    return next((i for i, n in enumerate(norms) if _OCR_TOTAL.match(n)), None)


def _coronation_table_rule(header, rows, table):
    """OCR tables are outstanding: dated total column or STATUS / AGE ANALYSIS."""
    from src.domain.cre_cleaner.core.table_type import OUTSTANDING

    norms = [n for n in header if n]
    if _ocr_total_index(norms) is not None or len(_OCR_MARKERS & set(norms)) >= 2:
        return OUTSTANDING, "Coronation OCR: dated outstanding amount / STATUS and AGE ANALYSIS columns"
    return None


class _CoronationAdapter(ControlledQuarterFiles, SourceLayoutAdapter):
    verified = False
    status_note = ("CORONATION: source-layout mapping (premium P1/P2, paid C1/C2, OCR); "
                   "no Bisola gold on disk, not checked against gold")
    premium_files_may_hold_claims = True
    claims_header_scan_all = True
    class_label_extra = {
        "COMMERCIAL VEHICLE": "Motor",
        "PRIVATE MOTOR": "Motor",
        "MOTOR": "Motor",
        "CONTRACTORS ALL RISK": "Engineering",
        "CONTRACTOR ALL RISK": "Engineering",
        "ERECTION ALL RISK": "Engineering",
        "MACHINERY BREAKDOWN": "Engineering",
        "MARINE VESSEL ANNUAL VOYAGE": "Marine Hull",
        "GEN ACC": "General Accident",
    }
    premium_alias_extra = {
        "sum_insured": ["GROSS SUM INSURED", "GROSS SUM"],
        "gross_premium": ["GROSS PREMIUM", "GROSS"],
        "class": ["CLASS OF BUSINESS", "CLASS"],
        "period_from": ["START DATE", "FROM"],
        "period_to": ["END DATE", "TO"],
    }
    claims_alias_extra = {
        "insured": ["ASSURED NAME"],
        "class": ["CLASS NAME"],
        "claim_no": ["CLAIM NO", "CLAIM NUMBER"],
        "period_from": ["POLICY FROM DATE"],
        "period_to": ["POLICY TO DATE"],
        "date_of_loss": ["LOSS DATE"],
        "details": ["LOSS DESCRIPTION"],
        "total_claims": ["PAYOUT AMOUNT TOTAL"],
        "ppn_ret": ["RETENTION %", "RETAINED %"],
        "amount_ret": ["RETENTION N", "RETAINED AMOUNT", "RETENTION"],
        "ppn_treaty": ["TREATY %", "TREATY RECOVERABLE %"],
        "amount_treaty": ["TREATY AMOUNT", "TREATY RECOVERABLE AMOUNT", "TREATY RECOVERABLE", "TREATY"],
        "ppn_fac": ["FAC %", "FAC RECOVERABLE %"],
        "amount_fac": ["FAC AMOUNT", "FAC RECOVERABLE N", "FAC RECOVERABLE", "FAC"],
        "sum_insured": ["INSURED SUM", "SUM INSURED"],
    }
    claims_total_fallback = {"paid": ("PAYOUT AMOUNT TOTAL",), "outstanding": ()}

    # --- reading ------------------------------------------------------------
    def read_source_workbook(self, path):
        # Standard reader, not calamine: the 2023–25 premium tabs declare all
        # 16,384 columns, which calamine reads in ~45 s per file against ~5 s
        # (Q4 2025). .xlsb (OCR as at March 2025) still goes to calamine.
        from src.domain.cre_cleaner.io.excel import read_source_workbook

        return read_source_workbook(path)

    # --- tables -------------------------------------------------------------
    def classify_source_tab(self, file, sheet, rows):
        return retype_tables(file, sheet, rows, self.table_vocab, _coronation_table_rule)

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        out = list(rows)
        dropped = [i + 1 for i, r in enumerate(out) if r and _column_number_row(r)]
        for r1 in dropped:
            out[r1 - 1] = [None] * len(out[r1 - 1])
        if dropped:
            exceptions.append(ExceptionRecord(
                "INFO", "column_number_row_skipped", path.name, sheet, dropped[0],
                f"Column-number rows (1, 2, 3 …) above repeated headers not read: rows {dropped[:10]}"))
        return out

    # --- columns ------------------------------------------------------------
    def claims_exclude(self, field, norm):
        if norm in {"PAYOUT AMOUNT TOTAL HC", "TOTAL RECOVERABLE", "COINSURANCE RECOVABLE",
                    "COINSURANCE RECOVERABLE", "NET CLAIMS AMOUNT", "CLAIM ESTIMATE INDEMNITY",
                    "PROCESS PERIOD", "NLIP %", "NLIP AMOUNT", "PRODUCT NAME", "PRODUCT CODE"}:
            return True
        return super().claims_exclude(field, norm)

    def map_claims_columns(self, header, aliases, sheet_type=""):
        cm = super().map_claims_columns(header, aliases, sheet_type)
        norms = _norms(header)
        # OCR: the dated outstanding amount is the total.
        ocr = _ocr_total_index(norms)
        if ocr is not None:
            cm.mapping["total_claims"] = ocr
        # Explicit 'X %' / 'X AMOUNT' pairs (2025) win over single recoverable columns.
        for kind, pct, amt in (("treaty", "TREATY %", "TREATY AMOUNT"), ("fac", "FAC %", "FAC AMOUNT")):
            if pct in norms and amt in norms:
                cm.mapping["ppn_" + kind], cm.mapping["amount_" + kind] = norms.index(pct), norms.index(amt)
        # 2019: 'Payout Amount Total | % | Retention | % | Treaty | % | Fac' —
        # each bare % is the proportion of the amount after it.
        for kind, label in (("ret", "RETENTION"), ("treaty", "TREATY"), ("fac", "FAC")):
            i = cm.get("amount_" + kind)
            if (i is not None and i > 0 and norms[i] == label and norms[i - 1] == "%"
                    and cm.get("ppn_" + kind) in (None, i + 1)):
                cm.mapping["ppn_" + kind] = i - 1
        # A % column named for its band wins over the shared rule that takes the
        # share column after each amount ('Retention (N) | Treaty Recoverable (%)',
        # 'Treaty Recoverable | Fac %' are the next band's shares).
        for kind, labels in _NAMED_PPN.items():
            j = next((k for k, n in enumerate(norms) if n in labels), None)
            if j is not None:
                cm.mapping["ppn_" + kind] = j
        if "RETAINED AMOUNT" in norms:
            cm.mapping["amount_ret"] = norms.index("RETAINED AMOUNT")
        for field in ("ppn_ret", "ppn_treaty", "ppn_fac"):
            j = cm.get(field)
            if j is not None and not is_share_header(norms[j]) and norms[j] != "%":
                del cm.mapping[field]
        return cm

    def premium_header_cells(self, raw_rows, header_at, header, group_row):
        header = list(header)
        norms = _norms(header)
        if "CESSION" in norms and ("RETAIN" in norms or "RETAIN PREMIUM" in norms):
            # P1: 'GROSS SUM' / 'INSURED', 'GROSS' / 'PREMIUM', 'RETAIN' / 'PREMIUM' …
            # (the engine has usually merged the second header row already).
            names = {"GROSS SUM": "SUM INSURED", "GROSS SUM INSURED": "SUM INSURED",
                     "GROSS": "GROSS PREMIUM", "RETAIN": "RETENTION PREMIUM",
                     "RETAIN PREMIUM": "RETENTION PREMIUM", "CEDED": "TREATY PREMIUM",
                     "CEDED PREMIUM": "TREATY PREMIUM", "LOCAL FAC": "FAC PREMIUM",
                     "LOCAL FAC PREMIUM": "FAC PREMIUM",
                     # EML (estimated maximum loss) is not a band sum insured.
                     "EML SUM": "EML VALUE", "EML SUM INSURED": "EML VALUE", "% EML": "EML RATE"}
            for i, n in enumerate(norms):
                if n in names:
                    header[i] = names[n]
            for i, n in enumerate(norms):
                if n == "%" and i + 1 < len(norms):
                    nxt = normalize_header(header[i + 1])
                    band = {"RETENTION PREMIUM": "RETENTION", "TREATY PREMIUM": "TREATY",
                            "FAC PREMIUM": "FAC"}.get(nxt)
                    if band:
                        header[i] = band + " PPN"
        if "CORONATION SI" in norms:
            # P2: Coronation SI / Premium / % = retention; Treaty SI / Premium /
            # Cession(%) = treaty; FAC% only (no FAC SI or premium in the source).
            for i, n in enumerate(norms):
                new = {"CORONATION SI": "RETENTION SUM INSURED", "CORONATION PREMIUM": "RETENTION PREMIUM",
                       "TREATY SI": "TREATY SUM INSURED", "TREATY PREMIUM": "TREATY PREMIUM",
                       "TREATY CESSION %": "TREATY PPN", "FAC%": "FAC PPN", "FAC %": "FAC PPN",
                       "TREATY LIMIT": "CAPACITY LIMIT"}.get(n)
                if new:
                    header[i] = new
                elif n == "%" and i > 0 and norms[i - 1] == "CORONATION PREMIUM":
                    header[i] = "RETENTION PPN"
        return super().premium_header_cells(raw_rows, header_at, header, group_row)

    def map_premium_columns(self, header, group_row, **kwargs):
        cm = super().map_premium_columns(header, group_row, **kwargs)
        # P2 has FAC% but no FAC SI or premium, so no FAC block forms around it.
        norms = _norms(header)
        if cm.fac_ppn is None and "FAC PPN" in norms:
            cm.fac_ppn = norms.index("FAC PPN")
        return cm

    # --- classes ------------------------------------------------------------
    def sheet_class_hint(self, sheet, class_hint):
        # Report names ('PREMIUM BORD OCT - DEC 2025', 'Settled Claims_…', 'OCR AS AT …')
        # are not classes; a tab named for a class (FIRE, Gen. Acc.) is.
        mapped = normalize_class_label(sheet, self.class_map())
        if mapped not in {"", "Other"} and not is_unresolved_class(mapped) and not _REPORT_TAB.search(
                normalize_header(sheet)):
            return mapped
        return ""

    def premium_banner_label(self, cells, text):
        if _UY_BANNER.match(normalize_header(text)):
            return ""
        return None

    def _pick(self, values, cm):
        for v in values:
            text = clean_text(v)
            if not text or not any(ch.isalpha() for ch in text) or _UY_BANNER.match(normalize_header(text)):
                continue
            mapped = normalize_class_label(text, cm)
            if mapped not in {"", "Other"} and not is_unresolved_class(mapped):
                return mapped, text
        return None

    def resolve_premium_class(self, *, sheet_hint, section, raw, **context):
        cm = self.class_map()
        hit = self._pick([section], cm) or self._pick([raw], cm) or self._pick([sheet_hint], cm)
        if hit:
            source = "banner" if section and hit[1] == clean_text(section) else (
                "column" if raw and hit[1] == clean_text(raw) else "sheet")
            return hit[0], source, hit[1]
        fallback = next((clean_text(v) for v in (raw, section, sheet_hint)
                         if clean_text(v) and any(ch.isalpha() for ch in clean_text(v))), "")
        return fallback, "column" if fallback and fallback == clean_text(raw) else "banner", fallback

    def resolve_claims_class(self, *, row, cmap, sheet_hint, section, **context):
        cm = self.class_map()
        i = cmap.get("class")
        raw = clean_text(row[i]) if i is not None and i < len(row) else ""
        hit = self._pick([section], cm) or self._pick([raw], cm) or self._pick([sheet_hint], cm)
        if hit:
            source = "banner" if section and hit[1] == clean_text(section) else (
                "column" if raw and hit[1] == raw else "sheet")
            return hit[0], source, hit[1]
        return raw if any(ch.isalpha() for ch in raw) else "", "column", raw

    def claims_header_cells(self, header, band_row, data_rows=(), notes=None):
        cells = super().claims_header_cells(header, band_row, data_rows=data_rows, notes=notes)
        norms = _norms(cells)
        if "NLIP AMOUNT" in norms and notes is not None:
            # NLIP (local pool) is a further cession: kept out of RET / TREATY / FAC.
            i = norms.index("NLIP AMOUNT")
            values = [v for v in (parse_number(r[i]) for r in data_rows if i < len(r)) if v]
            if values:
                notes.append(("WARN", "nlip_left_out",
                              f"NLIP cession kept out of the RET / TREATY / FAC bands: {len(values)} rows, "
                              f"{sum(values):,.2f} in total"))
        return cells


class CoronationScibAdapter(_CoronationAdapter):
    cedant = "CORONATION"
    broker = "SCIB"
    status_note = _CoronationAdapter.status_note + "; SCIB: motor only"
    # Pairs compared 2026-10-28: same rows and totals (premium, paid, outstanding).
    superseded_files = {
        (2020, 1): {"Premium & Claims Bordereaux. Q1.xlsx":
                    "same rows and totals as 'Premium & Claims Bordereaux Q1.xlsx'"},
        (2020, 4): {"Premium & Claims Bordeareaux. Q4.xlsx":
                    "same rows and totals as 'Premium & Claims Bordeareaux Q4.xlsx'"},
    }


class CoronationUaibAdapter(_CoronationAdapter):
    cedant = "CORONATION"
    broker = "UAIB"
    # Pairs compared 2026-10-28 (rows and totals per type); the choice is logged.
    superseded_files = {
        (2020, 1): {"Q1 2020 TREATY BORDEREAUX.xlsx":
                    "'Q1 2020 TREATY BORDEREAUX (Pn update).xlsx' is the updated copy (same rows and totals)"},
        (2023, 3): {"REINSURANCE BORDEREAUX Q3.xlsx":
                    "'Copy of TREATY BORDEAUX_Q3_2023_Final_UAIB_-revised.xlsx' is the revised copy "
                    "(same rows and totals)"},
        (2024, 2): {"TREATY BORDEAUX Q2 FINAL 2024-TUAIB.xlsx": (
            "WARN", "'Copy of TREATY BORDEAUX Q2 FINAL 2024-UAIBL.xlsx' is the later save (2024-09-02 vs "
            "2024-08-27); same 541 premium and 91 paid rows, but treaty premium differs "
            "(771,222,200.09 vs 759,483,232.41) — review")},
        (2025, 4): {"OCR AS AT 31ST DECEMBER 2025 - UAIB.XLSX": (
            "WARN", "its class tabs and 'Sheet1 (4)' repeat the same claims (4,096 rows, 12.64bn); "
            "'OCR AS AT 31ST DECEMBER 2025.xlsx' holds each claim once (2,050 rows, 6.33bn)")},
    }
