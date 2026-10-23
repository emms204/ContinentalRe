"""CHI/SCIB source layouts, including page tables extracted from PDFs."""
from __future__ import annotations

import re
from collections import Counter
from math import isclose

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.core.map_columns import PremiumLayoutRules, merge_group_subheaders
from src.domain.cre_cleaner.core.normalize import clean_text, normalize_header, parse_date, parse_number
from src.domain.cre_cleaner.models import ExceptionRecord


class ChiPremiumRules(PremiumLayoutRules):
    def band_kind(self, label):
        if re.match(r"^CHI(?: |$)", label.upper()):
            return "ret"
        return super().band_kind(label)

    def block_for_ppn(self, pi, norms, si_idxs, prem_idxs):
        # CHI tables consistently use SI / % / PREMIUM inside each band.
        # Bound pairing to the immediate neighbours, never the next band.
        if pi - 1 in si_idxs and pi + 1 in prem_idxs:
            return pi, pi - 1, pi + 1
        return self._labelled_block(pi, norms, si_idxs, prem_idxs)

    def _labelled_block(self, pi, norms, si_idxs, prem_idxs):
        kind = self.band_kind(norms[pi])
        if kind:
            si = [i for i in si_idxs if abs(i - pi) <= 3 and self.band_kind(norms[i]) == kind]
            prem = [i for i in prem_idxs if abs(i - pi) <= 3 and self.band_kind(norms[i]) == kind]
            if len(si) == len(prem) == 1:
                return pi, si[0], prem[0]
        return super().block_for_ppn(pi, norms, si_idxs, prem_idxs)


def _header_text(value):
    return re.sub(r"^UNDERWRITING YEAR\s+\d{4}\s*", "", normalize_header(value))


def _converted_header(row):
    compact = {re.sub(r"\s+", "", _header_text(v)) for v in row}
    return {"CESSIONNO", "POLICYNO", "INSUREDNAME"} <= compact


def _canonical_header(row, band):
    # Q4 has separate band/header rows; Q1 embeds decorated band labels in
    # every header cell. Normalize both without moving any source columns.
    # Only allocation labels belong in the band row. Report titles must
    # never replace identity/date column headers.
    band = [v if any(t in _header_text(v) for t in
            ("GROSS VALUE", "RETENTION", "SURPLUS", "QUOTA SHARE", "REINSURANCE"))
            and "BORDEREAU" not in _header_text(v) else None for v in (band or [])]
    cells = merge_group_subheaders(band, row) if any(band) else list(row)
    result = []
    aliases = {"CESSIONNO": "CESSION NO", "SUBRIS K": "SUBRISK", "SUBRIS": "SUBRISK",
               "POLICYNO": "POLICY NO", "DNCNNO": "DEBIT NOTE", "INSUREDNAME": "INSURED NAME"}
    for value in cells:
        h = _header_text(value)
        h = aliases.get(h, h)
        h = h.replace("GROSS VALUE", "GROSS").replace("RETENTION CAPACITY", "RETENTION")
        if h.endswith(" %"):
            h = h[:-2] + " PPN"
        result.append(h)
    for i, h in enumerate(result[:-1]):
        if h == "INSURANCE PERIOD" and result[i + 1] in {"", "INSURANCE PERIOD"}:
            result[i:i + 2] = ["FROM", "TO"]
            break
    return result


# These converters sometimes put two complete, dash-separated dates in one
# cell. Split only when each date parses independently; never guess a date.
_DATE_PAIR = re.compile(r"^(\d{1,2}-[A-Za-z]{3}-\d{4})\s+(\d{1,2}-[A-Za-z]{3}-\d{4})$")


class ChiScibAdapter(QuarterlyWorkbookAdapter):
    cedant = "CHI"
    broker = "SCIB"
    premium_layout_rules = ChiPremiumRules()
    premium_alias_extra = {"policy_no": ["POLICYNO"], "insured": ["INSUREDNAME"],
                           "debit_note": ["DNCNNO"], "period_from": ["START"], "period_to": ["END"]}
    claims_alias_extra = {
        "period_from": ["STARTDATE"], "period_to": ["ENDDATE"],
        "details": ["NATURE OF LOSS", "NATURE OF THE LOSS", "CAUSE OF CLAIM"],
        "amount_ret": ["OUR RETENTION NET CLAIM"],
        "amount_treaty": ["1ST 2ND SUPPLUS", "Q REINSURES"],
        "ppn_ret": ["RETENTION PROP"],
        "ppn_treaty": ["TREATY PROP PPN", "TREATY PROP %", "Q RE POP PPN", "Q RE POP %"],
    }
    settings = {"uw_year_from_start_date": True}
    verified = False
    status_note = "CHI SCIB: native and converted PDF tables; not checked against Bisola gold"

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        if sheet in {"_Document", "_Provenance", "_Review"}:
            exceptions.append(ExceptionRecord(
                "INFO", "converter_metadata_skipped", path.name, sheet, 0,
                "PDF converter metadata retained in source; excluded from transaction parsing"))
            if sheet == "_Review":
                warnings = Counter(clean_text(r[1]) for r in rows[1:] if len(r) > 1
                                   and clean_text(r[0]).upper() in {"WARNING", "ERROR"})
                if warnings:
                    exceptions.append(ExceptionRecord(
                        "WARN", "converter_review_warnings", path.name, sheet, 0,
                        "Source converter review requires attention: " + ", ".join(
                            f"{code}={count}" for code, count in sorted(warnings.items()))
                        + "; see original _Review sheet; source figures have not been corrected"))
            return []
        result = [list(r) for r in rows]
        period_col = None
        for i, row in enumerate(rows):
            if _converted_header(row):
                result[i] = _canonical_header(row, rows[i - 1] if i else None)
                period_col = next((j for j, h in enumerate(result[i]) if h == "INSURANCE PERIOD"), None)
                exceptions.append(ExceptionRecord(
                    "INFO", "chi_converted_header_normalized", path.name, sheet, i + 1,
                    "Compact PDF labels and allocation bands normalized; source row/column positions preserved"))
            elif period_col is not None and period_col < len(row):
                match = _DATE_PAIR.fullmatch(clean_text(row[period_col]))
                if match and all(parse_date(v) is not None for v in match.groups()):
                    result[i][period_col] = " to ".join(match.groups())
        self._source_values(result, path, sheet, exceptions, source_rows=rows)
        return result

    def _source_values(self, rows, path, sheet, exceptions, *, source_rows):
        # No column shifts: a synthetic UW column is appended only when
        # explicit standalone or decorated source-year evidence exists.
        year_re = re.compile(r"(?:UNDERWRITING YEAR|UW YEAR|UW YR|UY)\s*(?:19|20)\d{2}|(?:19|20)\d{2}\s*(?:UY|UW YEAR)")
        years = {}
        for i, row in enumerate(source_rows):
            found = set()
            decorated_header = _converted_header(row)
            for j, value in enumerate(row):
                text = normalize_header(value)
                match = year_re.match(text) if decorated_header else year_re.fullmatch(text)
                if match:
                    found.add(int(re.search(r"(?:19|20)\d{2}", match.group()).group()))
                elif text in {"UNDERWRITING YEAR", "UW YEAR", "UW YR", "UY"} and j+1 < len(row):
                    year = clean_text(row[j+1])
                    if re.fullmatch(r"(?:19|20)\d{2}", year):
                        found.add(int(year))
            if len(found) == 1:
                years[i] = found.pop()
        width = max((len(r) for r in rows), default=0)
        current_year = None
        columns = None
        for i, row in enumerate(rows):
            current_year = years.get(i, current_year)
            norms = [normalize_header(v) for v in row]
            claims_header = "CLAIM NO" in norms and "POLICY NO" in norms
            premium_header = "POLICY NO" in norms and "INSURED NAME" in norms and any("PREMIUM" in n for n in norms)
            if claims_header or premium_header:
                is_claims = claims_header
                columns = (self.map_claims_columns(row, self.claims_aliases(), "claims") if is_claims
                           else self.map_premium_columns(row, rows[i-1] if i else None))
                uw_key = "uw_yr" if is_claims else "uw_year"
                append_uw = bool(years) and columns.get(uw_key) is None
                if append_uw:
                    row.extend([None] * (width-len(row)))
                    row.append("UW YEAR")
                continue
            if columns is None:
                continue
            insured_col = columns.get("insured")
            if insured_col is None or insured_col >= len(row) or not clean_text(row[insured_col]):
                continue
            if append_uw and current_year is not None:
                row.extend([None] * (width-len(row)))
                row.append(current_year)
                exceptions.append(ExceptionRecord("INFO", "chi_uw_year_from_banner", path.name, sheet, i+1,
                    f"UW year {current_year} copied from explicit source banner"))
            def value(col):
                return parse_number(row[col]) if col is not None and col < len(row) else None
            if is_claims:
                triples = [(columns.get("ppn_"+k), columns.get("amount_"+k), columns.get("total_claims"))
                           for k in ("ret", "treaty", "fac")]
            else:
                triples = [(pi, pr, columns.get("gross_premium")) for pi, pr in
                    [(columns.ret_ppn, columns.ret_prem), (columns.sur_ppn, columns.sur_prem),
                     (columns.fac_ppn, columns.fac_prem)] + [(p, pr) for _,p,_,pr in columns.extra_treaty_blocks]]
            for pi, amount_col, total_col in triples:
                pct, amount, total = value(pi), value(amount_col), value(total_col)
                if pct is not None and 0 < abs(pct) <= 1 and amount is not None and total and isclose(amount/total, pct, rel_tol=1e-4, abs_tol=1e-8):
                    row[pi] = pct * 100
                    exceptions.append(ExceptionRecord("INFO", "chi_fractional_percentage_normalized", path.name, sheet, i+1,
                        f"Column {pi+1}: {pct} converted to {pct*100} percent points; confirmed by source amount / gross"))

    def premium_header_indices(self, rows, default):
        # Every PDF page can have its own width/date layout. Let the normal
        # parser build a fresh column map at each complete premium header.
        headers = [i for i, row in enumerate(rows)
                   if {"CESSION NO", "POLICY NO", "INSURED NAME", "GROSS PREMIUM"}
                   <= set(normalize_header(v) for v in row)]
        return sorted(set(default) | set(headers))
