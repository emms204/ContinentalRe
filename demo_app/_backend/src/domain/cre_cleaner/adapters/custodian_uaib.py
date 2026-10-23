"""Evidence-checked repairs for damaged Custodian/UAIB claims headers."""
from __future__ import annotations

import re

from src.domain.cre_cleaner.core.normalize import clean_text, normalize_header, parse_number


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
