"""LASACO adapters (Feybil, Jomola, Jordans) — LASACO rules only (IMPL-20260929-04).

LASACO outstanding / premium tabs use a spanning band row with a sub-row::

  row1: … GROSS LOSS RESERVE | RETENTION | | TREATY |
  row2: …                    | %         | AMOUNT | % | AMOUNT

The shared merge mechanism (``core.map_columns.merge_group_subheaders``,
``band_subrow_layout``, on by default) handles it; stated explicitly here.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.adapters.lasaco_jordans import JordansLayouts
from src.domain.cre_cleaner.core.detect import SheetTypeRules
from src.domain.cre_cleaner.core.map_columns import normalize_header
from src.domain.cre_cleaner.core.normalize import clean_text, parse_number
from src.domain.cre_cleaner.models import ExceptionRecord


# LASACO premium tabs may carry only the cedant's share columns ('OUR SHARE
# SI' / 'OUR SHARE OF G PREM'): used for TSI / GP only when no policy-level
# SUM INSURED / GROSS PREMIUM column exists (alias order: shared aliases
# first). Moved out of the shared alias table (IMPL-20260929-04).
LASACO_PREMIUM_ALIAS_EXTRA = {
    "sum_insured": ["OUR SHARE SI", "OUR SHARE SUM INSURED"],
    "gross_premium": ["OUR SHARE OF G PREM", "OUR SHARE GROSS PREMIUM"],
}


# Outstanding tabs from 2023 on: total in FINAL ADJUSTED AMOUNT OF CLAIMS, then
# PROPORTION | CLAIM pairs per band. The band is named in the row above
# (RETENTION | | TREATY | | FAC.) or in the header ('PROPORTION (RETENTION)').
# Without a band name the pairs stay unmapped: their order is not a band.
# Last in the list so a GROSS LOSS RESERVE column, when present, stays the total.
LASACO_CLAIMS_TOTAL_FALLBACK = ["FINAL ADJUSTED AMOUNT OF CLAIMS"]
_CLAIM_BANDS = {
    "RETENTION": "RETENTION", "RET": "RETENTION", "NET RETENTION": "RETENTION",
    "TREATY": "TREATY", "FAC": "FAC", "FACULTATIVE": "FAC",
}
_PROPORTION_BAND_RE = re.compile(r"PROPORTION (.+)")


# Outstanding tabs are named 'FIRE OUT.' / 'GEN ACC. OUT.' or just by class
# inside a file named '… OUTSTANDING …'.
LASACO_SHEET_RULES = SheetTypeRules(ost_tokens=frozenset({"OST", "OUT"}))


class _LasacoAdapter(QuarterlyWorkbookAdapter):
    band_subrow_layout = True
    premium_alias_extra = LASACO_PREMIUM_ALIAS_EXTRA
    sheet_type_rules = LASACO_SHEET_RULES
    outstanding_by_file_name = True
    # The two-row heading merger emits these names. Copy source proportions;
    # never derive them from amounts when the source percentage is absent.
    claims_alias_extra = {
        'ppn_ret': ['RETENTION PPN'],
        'ppn_treaty': ['TREATY PPN'],
        'ppn_fac': ['FAC PPN', 'FACULTATIVE PPN'],
    }

    def premium_header_cells(self, raw_rows, header_at, header, group_row):
        out = list(header)
        norms = [normalize_header(v) for v in header]
        for i, label in enumerate(norms[:-2]):
            if label != 'PROP' or not group_row or i >= len(group_row):
                continue
            band = _CLAIM_BANDS.get(normalize_header(group_row[i]), '')
            if band and norms[i + 1:i + 3] == ['SUM INSURED', 'PREMIUM']:
                out[i:i + 3] = [f'{band} PPN', f'{band} SUM INSURED', f'{band} PREMIUM']
        return out, group_row

    def reconcile_source_overlaps(self, result, source_paths):
        """Report competing outstanding balances, without choosing a version.

        Called within one reporting-period pipeline group. Distinct currencies,
        cover periods and repeated rows within a single file are not competing
        cross-file versions. Claims without references cannot establish identity.
        """
        groups = defaultdict(list)
        for row in result.outstanding_rows:
            if not clean_text(row.claim_no) or not clean_text(row.policy_no):
                continue
            key = (clean_text(row.claim_no).casefold(), clean_text(row.policy_no).casefold(),
                   clean_text(row.insured).casefold(), normalize_header(row.class_name),
                   row.period_from, row.period_to, row.audit.currency)
            groups[key].append(row)
        fields = ('total_claims', 'amount_ret', 'amount_treaty', 'amount_fac',
                  'ppn_ret', 'ppn_treaty', 'ppn_fac')
        labels = ('Gross reserve', 'Retention amount', 'Treaty amount', 'Facultative amount',
                  'Retention %', 'Treaty %', 'Facultative %')

        def differs(a, b):
            for field in fields:
                x, y = getattr(a, field), getattr(b, field)
                if x is None or y is None:
                    if x != y:
                        return True
                elif abs(x - y) > (1e-6 if field.startswith('ppn_') else .005):
                    return True
            return False

        for rows in groups.values():
            for row in rows:
                others = [other for other in rows
                          if other.audit.source_filename != row.audit.source_filename
                          and differs(row, other)]
                if not others:
                    continue
                versions = [row] + others
                evidence = '; '.join(
                    f'{v.audit.source_filename} / {v.audit.source_sheet} / row {v.audit.source_row}: '
                    + ', '.join(f'{label}={getattr(v, field)}' for field, label in zip(fields, labels))
                    for v in versions)
                result.exceptions.append(ExceptionRecord(
                    'WARN', 'lasaco_outstanding_source_conflict', row.audit.source_filename,
                    row.audit.source_sheet, row.audit.source_row,
                    f'Outstanding claim {row.claim_no!r}, policy {row.policy_no!r} '
                    f'has conflicting source balances ({row.audit.currency}). {evidence}. '
                    'Both versions remain included; choose the controlling file and re-run. '
                    'Do not add these balances together as separate claims.',
                ))

    def claims_aliases(self) -> Dict[str, List[str]]:
        aliases = super().claims_aliases()
        out = {k: list(v) for k, v in aliases.items()}
        out["total_claims"] = out.get("total_claims", []) + LASACO_CLAIMS_TOTAL_FALLBACK
        return out

    def claims_header_cells(
        self, header: Sequence[Any], band_row: Optional[Sequence[Any]],
        data_rows: Sequence[Sequence[Any]] = (), notes: Optional[list] = None,
    ) -> List[Any]:
        norms = [normalize_header(h) if h is not None else "" for h in header]
        if "CLAIM" not in norms:
            return list(header)
        above = [
            _CLAIM_BANDS.get(normalize_header(c) if c is not None else "", "")
            for c in (band_row or [])
        ]
        out = list(header)
        band = ""
        for i, n in enumerate(norms):
            m = _PROPORTION_BAND_RE.fullmatch(n)
            if n == "PROPORTION" or m:
                band = _CLAIM_BANDS.get(m.group(1), "") if m else (above[i] if i < len(above) else "")
                if band:
                    out[i] = f"{band} PPN"
                continue
            if n == "CLAIM" and band:
                out[i] = f"{band} AMOUNT"
            band = ""
        return out


class LasacoJomolaAdapter(_LasacoAdapter):
    cedant = "LASACO"
    broker = "JOMOLA"
    # Class tabs named 'GEN ACC' / 'ACC 1ST QRT 2019' / 'DIR& OFF LIABILITY'
    # match no tab-name rule and their headers say 'GROSS PREM.' / 'OUR
    # SHARE OF G. PREM.': premium is taken from the tab's content.
    recover_premium_from_content = True
    # 2019 Q1: a blank row between the band row and its PPN / PREMIUM subrow.
    band_subrow_after_blank = True
    verified = False
    status_note = "LASACO/JOMOLA: class/quarter returns; unverified"


class LasacoFeybilAdapter(_LasacoAdapter):
    cedant = "LASACO"
    broker = "FEYBIL"
    verified = False
    status_note = "LASACO/FEYBIL: quarterly premium/claims bordereaux; unverified"

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        """Fixed FEYBIL outstanding schema, gated by row and summary controls.

        The approved 15-column contract assigns J/K to retention, L/M to
        treaty and N/O to FAC. Equal summary totals are validation, never the
        evidence used to infer that order. Source cells are not modified.
        """
        if 'OUTSTANDING' not in normalize_header(path.stem):
            return rows
        signature = ['BROKERS AGENT', 'DATE OF NOTIFICATION OF LOSS', 'DATE OF LOSS INCIDENT',
                     'CLAIM NUMBER', 'POLICY NUMBER', 'NAME OF INSURED CLAIMANT',
                     'DESCRIPTION OF LOSS', 'PERIOD OF COVER', 'FINAL ADJUSTED AMOUNT OF CLAIMS',
                     'PROPORTION', 'CLAIM', 'PROPORTION', 'CLAIM', 'PROPORTION', 'CLAIM']
        headers = [i for i, row in enumerate(rows) if
                   [normalize_header(v) for v in row[:15]] == signature
                   and not any(clean_text(v) for v in row[15:])]
        if not headers:
            return rows
        header_at = headers[0]
        if header_at and any(normalize_header(v) in _CLAIM_BANDS
                             for v in rows[header_at - 1][9:15]):
            return rows  # authoritative band names use the existing labelled path

        def reject(detail):
            exceptions.append(ExceptionRecord('WARN', 'lasaco_outstanding_layout_unresolved',
                path.name, sheet, header_at + 1,
                'Outstanding allocation columns left unmapped: ' + detail))
            return rows

        if len(headers) != 1:
            return reject('multiple unlabelled blocks need separate column-role review')
        transactions = []
        for row in rows[header_at + 1:]:
            if not any(clean_text(v) for v in row[3:6]):
                continue  # blank rows and printed footer totals
            if len(row) < 15 or not all(clean_text(v) for v in row[3:6]):
                return reject('a source row has incomplete claim/policy/insured identity')
            numbers = [parse_number(v) for v in row[8:15]]
            if any(n is None and clean_text(v).upper() not in {'', 'NIL', 'N/A', 'NA', '-', '—'}
                   for v, n in zip(row[8:15], numbers)) or numbers[0] is None:
                return reject('a source reserve or allocation is not numeric')
            gross = numbers[0]
            for percent, amount in zip(numbers[1::2], numbers[2::2]):
                if percent is None and amount in (None, 0):
                    continue
                if percent is None or amount is None or not 0 <= percent <= 100:
                    return reject('a percentage/claim pair is incomplete or out of range')
                ratios = [percent / 100] + ([percent] if percent <= 1 else [])
                if not any(abs(gross * ratio - amount) <= .02 for ratio in ratios):
                    return reject('a percentage/claim pair does not reconcile to its gross reserve')
            if abs(gross - sum(v or 0 for v in numbers[2::2])) > .02:
                return reject('retention, treaty and FAC amounts do not reconcile to gross reserve')
            transactions.append(numbers)
        if not transactions:
            return reject('no complete source transactions found')

        from src.domain.cre_cleaner.io.excel import read_source_workbook
        books, _ = read_source_workbook(path)
        summary = next((data for name, data in books.items() if normalize_header(name) == 'SUMMARY'), [])
        controls = []
        for i, row in enumerate(summary):
            labels = [normalize_header(v) for v in row[:4]]
            if (labels[:3] != ['CLASS', 'GROSS CLAIM', 'RETENTION'] or len(labels) < 4
                    or labels[3] not in {'CLAIM DUE TO REINSURER TREATY', 'TREATY'}):
                continue
            controls.extend([parse_number(v) for v in r[1:4]] for r in summary[i + 1:]
                            if len(r) >= 4 and normalize_header(r[0]) == normalize_header(sheet))
        if len(controls) != 1 or any(v is None for v in controls[0]):
            return reject('one labelled SUMMARY control for this class is required')
        actual = [sum(r[i] or 0 for r in transactions) for i in (0, 2, 4)]
        if any(abs(a - b) > .02 for a, b in zip(actual, controls[0])):
            return reject('gross/retention/treaty totals differ from the labelled SUMMARY controls')
        prepared = [list(row) for row in rows]
        prepared[header_at][9:15] = ['RETENTION PPN', 'RETENTION AMOUNT', 'TREATY PPN',
                                   'TREATY AMOUNT', 'FAC PPN', 'FAC AMOUNT']
        exceptions.append(ExceptionRecord('INFO', 'lasaco_outstanding_layout_verified',
            path.name, sheet, header_at + 1,
            'FEYBIL 15-column layout: J/K retention, L/M treaty, N/O facultative; '
            'source percentage/amount pairs and class SUMMARY controls verified. '
            'Source proportions and amounts copied without deriving missing values.'))
        return prepared


class LasacoJordansAdapter(JordansLayouts, _LasacoAdapter):
    cedant = "LASACO"
    broker = "JORDANS"
    verified = False
    status_note = "LASACO/JORDANS: quarterly returns; unverified"
