"""Custodian & Allied Insurance via SCIB — first pass, not yet checked
against Bisola's cleaned output.

Layout seen in every SCIB return 2020–2025 (one workbook per quarter):
  PREMIUM / TREATY RETURNS tab, one block per class. The class name sits in
  column B of the band row above each header, e.g.
    band:   MARINE HULL | | Insurance Period | | | | CAI'S RETEN | CAI'S | Surplus Treaty | Surplus Treaty | Treaty | Facultative
    header: Policy No | Insured Name | (from) | (to) | Sum Insured. | Gross Premium | Sum Insured | Rate | Sum Insured | Premium | Rate | Sum Insured
  Retention has SI + rate only (no premium). The Facultative column is headed
  'Sum Insured' (once 'share') but holds a percentage — mapped to FAC %.
  Block totals are unlabelled rows under Treaty Premium.
  CLAIMS PAID tab: Insured | Policy | Claims Number | Start | End | Loss Date |
  Payment Date | Amount (100%) | TTY % | TTY amount | Description, with class
  banners below the header. Some quarters (2020 Q2/Q4, 2021 Q4, 2023 Q1,
  2024 Q3) omit header rows entirely; the positional layout above is used
  then, only when the first data row has the expected shape.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from cre_cleaner.core.map_columns import ColumnMap, map_simple_columns
from cre_cleaner.models import ExceptionRecord
from cre_cleaner.core.normalize import clean_text, normalize_header, parse_number

_PREMIUM_HEADER = [None, "Policy No", "Insured Name", "Insurance Period", None, "Sum Insured.",
                   "Gross Premium", "Sum Insured", "Rate", "Sum Insured", "Premium", "Rate",
                   "Sum Insured"]
_PREMIUM_BAND = [None, None, None, None, None, None, None, "CAI'S RETEN", "CAI'S",
                 "Surplus Treaty", "Surplus Treaty", "Treaty", "Facultative"]
_CLAIMS_HEADER = [None, "Insured Name", "Policy Number", "Claims Number", "Start Date", "End Date",
                  "Loss Date", "Payment Date", "Amount", "TTY %", "TTY amount", "Description"]


def _note_once(exceptions: list, rec: ExceptionRecord) -> None:
    if not any(
        e.reason == rec.reason and e.source_filename == rec.source_filename and e.source_sheet == rec.source_sheet
        for e in exceptions
    ):
        exceptions.append(rec)


class CustodianScibAdapter(QuarterlyWorkbookAdapter):
    cedant = "CUSTODIAN"
    broker = "SCIB"
    verified = False
    status_note = "Custodian SCIB layout mapped from 2020–2025 returns; no Bisola gold on disk"

    claims_alias_extra = {
        "claim_no": ["CLAIMS NUMBER", "CLAIMS NO"],
        "total_claims": ["AMOUNT"],
        "amount_treaty": ["TTY AMOUNT"],
        "period_from": ["START DATE"],
        "period_to": ["END DATE"],
        "details": ["DESCRIPTION"],
    }

    def map_premium_columns(
        self,
        header: Sequence[Any],
        group_row: Optional[Sequence[Any]],
        *,
        path: Optional[Path] = None,
        sheet: str = "",
        exceptions: Optional[list] = None,
    ) -> ColumnMap:
        norms = [normalize_header(h) if h is not None else "" for h in header]
        bands = [normalize_header(g) if g is not None else "" for g in (group_row or [])]
        if "GROSS PREMIUM" not in norms:
            return self.detect_premium_allocation_blocks(header, group_row)
        gp = norms.index("GROSS PREMIUM")
        if not any("RETEN" in b for b in bands):
            # Some returns (e.g. 2025 Q4) drop the band row; accept only the
            # exact Custodian column sequence.
            if norms[gp + 1: gp + 7] != ["SUM INSURED", "RATE", "SUM INSURED", "PREMIUM", "RATE", "SUM INSURED"]:
                return self.detect_premium_allocation_blocks(header, group_row)
            offset = gp - _PREMIUM_HEADER.index("Gross Premium")
            bands = [""] * max(offset, 0) + [normalize_header(b) if b else "" for b in _PREMIUM_BAND]
            if exceptions is not None and path is not None:
                _note_once(exceptions, ExceptionRecord(
                    "INFO", "band_row_missing_positional_layout", path.name, sheet, 0,
                    "No RET/TREATY/FAC band row above the header; standard Custodian band order assumed",
                ))
        cm = map_simple_columns(header, self.premium_aliases(), exclude=self.premium_exclude)
        cm.mapping["gross_premium"] = gp
        si = next((i for i in range(gp - 1, -1, -1) if norms[i].startswith("SUM INSURED")), None)
        if si is not None:
            cm.mapping["sum_insured"] = si
        # Period: header 'Insurance Period' (or, in older returns, only the
        # band row) over two cells — FROM then TO.
        if cm.get("period") is None and cm.get("period_from") is None:
            pi = next((i for i, b in enumerate(bands) if "PERIOD" in b), None)
            if pi is not None:
                cm.mapping["period_from"], cm.mapping["period_to"] = pi, pi + 1
        elif cm.get("period") is not None:
            pi = cm.mapping.pop("period")
            cm.mapping["period_from"], cm.mapping["period_to"] = pi, pi + 1

        band = ""
        fac_rate_note = False
        for i in range(gp + 1, len(norms)):
            b = bands[i] if i < len(bands) else ""
            if b:
                band = "ret" if ("RETEN" in b or b.startswith("CAI")) else (
                    "fac" if "FACULTATIVE" in b else ("treaty" if ("SURPLUS" in b or "TREATY" in b) else "")
                )
            h = norms[i]
            if not band or not h:
                continue
            if band == "fac":
                # Header says 'Sum Insured'/'share' but values are FAC percentages.
                cm.fac_ppn = i
                fac_rate_note = h.startswith("SUM INSURED")
                continue
            target = "ppn" if h == "RATE" else ("si" if h.startswith("SUM INSURED") else (
                "prem" if h == "PREMIUM" else ""))
            if target:
                prefix = "ret" if band == "ret" else "sur"
                if getattr(cm, f"{prefix}_{target}") is None:
                    setattr(cm, f"{prefix}_{target}", i)
        cm.sur_label = "SURPLUS TREATY"
        if fac_rate_note and exceptions is not None and path is not None:
            _note_once(exceptions, ExceptionRecord(
                "INFO", "fac_column_header_mismatch", path.name, sheet, 0,
                "Facultative column is headed 'Sum Insured' but holds percentages — mapped to FAC %",
            ))
        return cm

    def synthetic_header(
        self, sheet_type: str, first_data_row: Sequence[Any],
    ) -> Optional[Tuple[List[Any], Optional[List[Any]]]]:
        def text(i: int) -> str:
            return clean_text(first_data_row[i]) if i < len(first_data_row) else ""

        def num(i: int) -> bool:
            return i < len(first_data_row) and parse_number(first_data_row[i]) is not None

        if sheet_type == "premium":
            if "/" in text(1) and text(2) and num(5) and num(6):
                return list(_PREMIUM_HEADER), list(_PREMIUM_BAND)
            return None
        if "/" in text(2) and "CL" in text(3).upper() and num(8):
            return list(_CLAIMS_HEADER), None
        return None
