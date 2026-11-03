"""Custodian & Allied Insurance adapters (SCIB, UAIB) and their shared layout rules.

Shared premium bands
--------------------
Custodian source premium bands shared by SCIB and UAIB.

Only supplied amounts are mapped: retention premium and facultative amounts
are absent from this layout and must not be copied from gross or treaty.

SCIB layout
-----------
Custodian & Allied Insurance via SCIB — first pass, not yet checked
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

UAIB claims headers
-------------------
Evidence-checked repairs for damaged Custodian/UAIB claims headers.
"""
from __future__ import annotations

import re
from math import isclose
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.core.map_columns import ColumnMap, map_simple_columns
from src.domain.cre_cleaner.core.normalize import (
    clean_text,
    normalize_header,
    parse_number,
    parse_date,
)
from src.domain.cre_cleaner.models import ExceptionRecord


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


class CustodianLayoutMixin:
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


class CustodianScibAdapter(CustodianLayoutMixin, QuarterlyWorkbookAdapter):
    cedant = "CUSTODIAN"
    broker = "SCIB"
    verified = False
    status_note = "Custodian SCIB layout mapped from 2020–2025 returns; gold for 2020 on disk"

    claims_alias_extra = {
        "claim_no": ["CLAIMS NUMBER", "CLAIMS NO"],
        "total_claims": ["AMOUNT"],
        "amount_treaty": ["TTY AMOUNT"],
        # Treaty share in percent points (37.37), copied as is.
        "ppn_treaty": ["TTY %"],
        "period_from": ["START DATE"],
        "period_to": ["END DATE"],
        "details": ["DESCRIPTION"],
    }


    def map_claims_columns(self, header, aliases, sheet_type=""):
        from src.domain.cre_cleaner.core.normalize import normalize_header

        cm = super().map_claims_columns(header, aliases, sheet_type)
        # 2025 claims tabs: 'Amount | % | TTY amount'; the bare % is the treaty
        # share (92.53 x 99,937.91 = 92,469.64).
        i = cm.get("amount_treaty")
        if (cm.get("ppn_treaty") is None and i and i - 1 not in cm.mapping.values()
                and normalize_header(header[i - 1]) == "%"):
            cm.mapping["ppn_treaty"] = i - 1
        return cm

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        """Recover the mixed identity orders in headerless SCIB paid returns.

        Normalize B:D in memory only; dates and money stay in their original
        columns and every accepted/rejected source row is recorded explicitly.
        """
        if normalize_header(sheet) not in {"CLAIMS PAID", "CLAIM PAID", "PAID CLAIMS"}:
            return rows
        for row in rows:
            labels = {normalize_header(v) for v in row}
            if labels & {"POLICY NUMBER", "POLICY NO"} and labels & {"CLAIMS NUMBER", "CLAIM NUMBER", "CLAIM NO", "CLAIMS NO"}:
                return rows

        def text(row, i):
            return clean_text(row[i]) if i < len(row) else ""

        def claim(value):
            return bool(re.search(r"(?:^|/)CL/", value.upper())) and any(c.isdigit() for c in value)

        def policy(value):
            return bool(re.fullmatch(r"[A-Za-z0-9-]+(?:/[A-Za-z0-9-]+){2,}", value)) and not claim(value) and any(c.isdigit() for c in value)

        def name(value):
            return bool(re.search(r"[A-Za-z]", value)) and not policy(value) and not claim(value)

        def order(row):
            b, c, d = (text(row, i) for i in (1, 2, 3))
            if policy(b) and claim(c) and name(d):
                return (3, 1, 2)
            if name(b) and policy(c) and claim(d):
                return (1, 2, 3)
            return None

        # Leave the established insured-first-only fallback unchanged. This
        # additional recovery applies when the alternate policy-first layout
        # is evidenced in a headerless SCIB table.
        if not any(order(row) == (3, 1, 2) for row in rows):
            return rows
        result = [list(row) for row in rows]
        for index, row in enumerate(rows):
            identities = [text(row, i) for i in (1, 2, 3)]
            if sum(bool(v) for v in identities) < 2 or not any(text(row, i) for i in (8, 9, 10)):
                continue
            roles = order(row)
            dates = [parse_date(row[i]) if i < len(row) else None for i in range(4, 8)]
            amounts = [parse_number(row[i]) if i < len(row) else None for i in range(8, 11)]
            gross, pct, treaty = amounts
            valid_dates = (all(d is not None for d in dates)
                           and dates[0] <= dates[1] and dates[2] <= dates[3])
            numeric = all(a is not None for a in amounts)
            valid_share = (numeric and 0 <= pct <= 100
                           and isclose(gross * pct / 100, treaty, rel_tol=1e-9, abs_tol=.02))
            failed = [label for label, passed in (
                ("unrecognized identity order", roles is not None),
                ("missing, invalid or reversed dates", valid_dates),
                ("missing or nonnumeric amount", numeric),
                ("gross × treaty percentage does not match treaty amount", valid_share),
            ) if not passed]
            if failed:
                result[index] = [None] * len(row)
                exceptions.append(ExceptionRecord(
                    "WARN", "headerless_block_unresolved", path.name, sheet, index+1,
                    "Headerless SCIB payment not loaded: " + "; ".join(failed)))
                continue
            result[index][1:4] = [row[i] for i in roles]
            exceptions.append(ExceptionRecord(
                "WARN", "custodian_headerless_claim_recovered", path.name, sheet, index+1,
                f"Headerless payment recovered: source insured/policy/claim columns {tuple(i+1 for i in roles)}; "
                "normalized in memory to B/C/D; dates E:H and amounts I:K verified and preserved",
                amount=gross))
        return result


def _claim_id(value):
    return bool(re.search(r"(?:^|/)CL(?:/|\d)", clean_text(value).upper()))


def _standard_identity(row):
    return (len(row) > 3 and bool(clean_text(row[1]))
            and "/" in clean_text(row[2]) and not _claim_id(row[2])
            and _claim_id(row[3]) and not _claim_id(row[1]))


def _supported(rows, predicate):
    # Ignore class banners, subtotals and blank lines. Never infer a layout
    # from one record; require a strong majority of transaction evidence.
    candidates = [r for r in rows if len(r) > 10 and clean_text(r[2])
                  and clean_text(r[3]) and parse_number(r[8]) is not None][:50]
    return len(candidates) >= 2 and sum(predicate(r) for r in candidates) / len(candidates) >= .8


class CustodianUaibClaimsMixin:
    def claims_header_cells(self, header, band_row, data_rows=(), notes=None):
        result = list(super().claims_header_cells(header, band_row, data_rows=data_rows, notes=notes))
        norms = [normalize_header(v) for v in result]
        if len(norms) < 11:
            return result
        if (norms[1:4] == ["POLICY NUMBER", "CLAIMS NUMBER", "INSURED NAME"]
                and _supported(data_rows, _standard_identity)
                and not any(len(r) > 8 and parse_number(r[8]) is not None
                            and "/" in clean_text(r[1]) and _claim_id(r[2])
                            and clean_text(r[3]) for r in data_rows)):
            result[1:4] = ["Insured Name", "Policy Number", "Claims Number"]
            if notes is not None:
                notes.append(("WARN", "custodian_claim_identity_headers_recovered",
                              "Misordered identity headers corrected to Insured / Policy / Claim; "
                              "at least two source rows support the Custodian layout"))
        # 2023 Q2: the gross-claim column is labelled TTY amount, while
        # the actual share and treaty amount columns have no labels.
        if (norms[1:4] == ["INSURED NAME", "POLICY NUMBER", "CLAIMS NUMBER"]
                and norms[8] == "TTY AMOUNT" and not norms[9] and not norms[10]):
            def matches(row):
                gross, pct, treaty = (parse_number(row[i]) for i in (8, 9, 10))
                return (_standard_identity(row) and gross not in (None, 0)
                        and pct is not None and 0 <= pct <= 100 and treaty is not None
                        and abs(gross * pct / 100 - treaty) <= max(.01, abs(gross) * .000001))
            if _supported(data_rows, matches):
                result[8:11] = ["Claim amount", "TTY %", "TTY amount"]
                if len(result) > 11 and not norms[11]:
                    details = [r[11] for r in data_rows if len(r) > 11
                               and _standard_identity(r) and clean_text(r[11])]
                    if details and all(isinstance(v, str) and parse_number(v) is None for v in details):
                        result[11] = "DESCRIPTION"
                if notes is not None:
                    notes.append(("WARN", "custodian_claim_amount_headers_recovered",
                                  "Misplaced TTY amount header corrected to gross / treaty % / treaty amount; "
                                  "source rows verify gross × treaty % = treaty amount"))
            elif notes is not None:
                notes.append(("WARN", "custodian_claim_amount_headers_unresolved",
                              "Possible misplaced TTY amount header; source rows do not safely establish "
                              "gross / treaty % / treaty amount, so no positional repair applied"))
        return result

    def map_claims_columns(self, header, aliases, sheet_type=""):
        cm = super().map_claims_columns(header, aliases, sheet_type)
        norms = [normalize_header(v) for v in header]
        if (len(norms) > 10 and norms[9] == "%" and norms[10] == "TTY AMOUNT"
                and norms[8] in {"AMOUNT", "CLAIM AMOUNT"}):
            # A bare % belongs to the neighbouring TTY amount, not retention.
            if cm.get("ppn_ret") == 9:
                cm.mapping.pop("ppn_ret")
            cm.mapping["ppn_treaty"] = 9
        return cm


class CustodianUaibAdapter(CustodianUaibClaimsMixin, CustodianLayoutMixin, QuarterlyWorkbookAdapter):
    """UAIB uses class-only outstanding tabs with an explicit reporting banner."""

    cedant = "CUSTODIAN"
    broker = "UAIB"
    content_sheet_typing = True
    claims_alias_extra = {
        "period_to": ["END DATE"],
        "ppn_treaty": ["TTY %", "TREATY SHARE PPN"],
        "details": ["CLAIMS DETAIL", "LOSS DETAILS"],
    }
    # Keep the generic explicit total headers ahead of these layout-specific
    # fallbacks, and never use a paid amount as the outstanding reserve.
    claims_total_fallback = {
        "paid": ("CLAIM AMOUNT", "AMOUNT"),
        "outstanding": ("LOSS AMOUNT",),
    }

    def claims_exclude(self, field: str, norm: str) -> bool:
        # The 2021 paid layout labels its 100% claim amount simply "Amount".
        # Do not let fuzzy matching consume it as "Retention Amount" before
        # the type-specific total fallback can use it.
        return norm == "AMOUNT" and field in {"amount_ret", "amount_treaty", "amount_fac"}

    verified = False
    status_note = "Custodian/UAIB: quarterly combined workbooks; unverified"
