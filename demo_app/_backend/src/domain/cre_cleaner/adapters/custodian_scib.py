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

import re
from math import isclose

from src.domain.cre_cleaner.core.normalize import clean_text, normalize_header, parse_date, parse_number
from src.domain.cre_cleaner.models import ExceptionRecord

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.adapters.custodian_common import CustodianLayoutMixin


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
