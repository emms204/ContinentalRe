"""LASACO/JORDANS source-layout repairs, independent of reporting year.

All preparation is on row copies. Physical source coordinates, amounts and
proportions remain unchanged; allocation roles require named source bands.
"""
from __future__ import annotations

import re
from datetime import datetime, date
from src.domain.cre_cleaner.core.normalize import clean_text, normalize_header, parse_number
from src.domain.cre_cleaner.models import ExceptionRecord

_HEADERS = {
    'POLICYNO': 'POLICY NO', 'POLICY N': 'POLICY NO',
    'POLICY NO INCLUDING CO FAC REINSURANCE NO': 'POLICY NO',
    'LOSSDATE': 'LOSS DATE', 'PAYMENTDATE': 'DATE PAID',
    'LOSS DETAILS': 'DETAILS OF LOSS', 'NATURE OF LOSS': 'DETAILS OF LOSS',
    'DESCRIPTION': 'DETAILS OF LOSS', 'DESCRIPTION OF ACCIDENT': 'DETAILS OF LOSS',
    'PERIOD OF INS': 'PERIOD OF COVER', 'COVER PERIOD': 'PERIOD OF COVER',
    'AMOUNTS OF CLAIMS PAID': 'TOTAL CLAIMS PAID', 'TOTAL RESERVE': 'TOTAL RESERVE AMOUNT',
    'RESERVE': 'TOTAL RESERVE AMOUNT',
}
_BANDS = {
    'RETENTION': 'RETENTION', 'RENTENTION': 'RETENTION', 'RET': 'RETENTION',
    'NET RETENTION': 'RETENTION', 'TREATY': 'TREATY',
    'TREATY 1ST SURPLUS': 'TREATY', 'TREATY ACCOUNT FIRST SURPLUS': 'TREATY',
    'TREATY QUOTA SHARE': 'TREATY', 'FAC': 'FAC', 'FACULTATIVE': 'FAC',
    'FAC OUTWARD': 'FAC',
}
_SHARE = {'%', 'PPN', 'PPN %', 'PROP', 'PROPORTION', 'PROPORTION %'}
_AMOUNT = {'AMOUNT', 'AMT', 'CLAIM'}
_MONTHS = {'JANUARY':'JAN', 'FEBRUARY':'FEB', 'MARCH':'MAR', 'APRIL':'APR',
           'JUNE':'JUN', 'JULY':'JUL', 'AUGUST':'AUG', 'SEPTEMBER':'SEP',
           'OCTOBER':'OCT', 'NOVEMBER':'NOV', 'DECEMBER':'DEC'}
_MONTH_RE = re.compile(r'\b(' + '|'.join(_MONTHS) + r')\b', re.I)


def _header(row):
    names = [_HEADERS.get(normalize_header(v), normalize_header(v)) for v in row]
    return names if any(v in {'CLAIM NO', 'CLAIMS NO', 'CLAIM NUMBER', 'CLAIMS NUMBER'} for v in names) and any(v in {'POLICY NO', 'POLICY NUMBER'} for v in names) else None


def _band_row(row):
    """A band-only row cannot contain a policy, amount or other data cell."""
    cells = [normalize_header(v) for v in row if clean_text(v)]
    return bool(cells) and all(v in _BANDS for v in cells)


class JordansLayouts:
    claims_alias_extra = {
        'ppn_ret': ['RETENTION PPN'], 'amount_ret': ['RETENTION AMOUNT', 'RETENTION'],
        'ppn_treaty': ['TREATY PPN'], 'amount_treaty': ['TREATY AMOUNT'],
        'ppn_fac': ['FAC PPN', 'FACULTATIVE PPN'], 'amount_fac': ['FAC AMOUNT'],
        'date_of_loss': ['LOSSDATE'], 'paid_date': ['PAYMENTDATE'],
        'details': ['LOSS DETAILS', 'NATURE OF LOSS', 'DESCRIPTION', 'DESCRIPTION OF ACCIDENT'],
        'period': ['PERIOD OF INS', 'COVER PERIOD'],
        'total_claims': ['AMOUNTS OF CLAIMS PAID', 'TOTAL RESERVE', 'RESERVE'],
        'policy_no': ['POLICYNO'],
    }

    def claims_exclude(self, field, norm):
        # The shared fuzzy matcher can match bare AMOUNT against RETENTION
        # AMOUNT. A source band must name the role before an amount is mapped.
        if field in {'amount_ret', 'amount_treaty', 'amount_fac'} and norm in _AMOUNT | _SHARE:
            return True
        if field.startswith('ppn_') and not (norm.endswith('PPN') or norm.endswith('%')):
            return True
        return super().claims_exclude(field, norm)

    def claims_header_cells(self, header, band_row, data_rows=(), notes=None):
        header = super().claims_header_cells(header, band_row, data_rows, notes)
        out = list(header)
        for c, name in enumerate(header):
            match = re.fullmatch(r'(.+) (PPN|PROPORTION|AMOUNT|AMT)', normalize_header(name))
            if match and match[1] in _BANDS:
                out[c] = _BANDS[match[1]] + (' PPN' if match[2] in {'PPN', 'PROPORTION'} else ' AMOUNT')
        return out

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        out = [list(row) for row in rows]
        # Some premium Start Date columns hold both dates, with a blank
        # Expiry Date. The shared period parser splits them once the source's
        # full hyphenated month spellings are made readable.
        for h, row in enumerate(rows):
            norms = [normalize_header(v) for v in row]
            if not (set(norms) & {'POLICY NO', 'POLICY N', 'POLICY NUMBER'}
                    and set(norms) & {'GROSS PREMIUM', 'GRSS PREMIUM', 'OUR SHARE OF G PREM'}):
                continue
            cols = [c for c, n in enumerate(norms) if n in {
                'START DATE', 'END DATE', 'EXPIRY DATE', 'INSURANCE PERIOD', 'FROM', 'TO'}]
            for i in range(h + 1, len(rows)):
                if _header(rows[i]):
                    break
                for c in cols:
                    if c < len(rows[i]) and isinstance(rows[i][c], str):
                        out[i][c] = _MONTH_RE.sub(lambda m: _MONTHS[m[0].upper()], rows[i][c])
        header_at = [i for i, row in enumerate(rows) if _header(row)]
        for index, h in enumerate(header_at):
            names = _header(rows[h])
            end = header_at[index + 1] if index + 1 < len(header_at) else len(rows)
            out[h] = names[:]
            above = []
            for b in range(h - 1, max(-1, h - 4), -1):
                if _header(rows[b]):
                    break
                if any(isinstance(v, (int, float, date)) for v in rows[b] if v is not None):
                    break
                if any(normalize_header(v) in _BANDS for v in rows[b]):
                    above = rows[b]
                    break
            below = rows[h + 1] if h + 1 < end else []
            below_names = {normalize_header(v) for v in below}
            has_subrow = bool(below_names & _SHARE and below_names & _AMOUNT
                              and not any(isinstance(v, (int, float, date)) for v in below if v is not None))
            band = below if _band_row(below) else above
            bands = [_BANDS.get(normalize_header(v), '') for v in band]
            bands += [''] * max(0, len(names) - len(bands))
            unresolved = []
            if has_subrow:
                # The source can reverse AMOUNT/PROPORTION inside individual
                # bands. Merge by each child's label, never by pair order.
                out[h] += [''] * max(0, len(below) - len(out[h]))
                active = ''
                for c in range(len(out[h])):
                    parent = names[c] if c < len(names) else ''
                    if parent:
                        active = _BANDS.get(parent, '')
                    child = normalize_header(below[c]) if c < len(below) else ''
                    if child in _SHARE | _AMOUNT:
                        if active:
                            out[h][c] = active + (' PPN' if child in _SHARE else ' AMOUNT')
                        else:
                            unresolved.append(c + 1)
                            out[h][c] = 'UNRESOLVED BAND VALUE'
                out[h + 1] = [None] * len(below)
            for c, name in enumerate(names):
                if name in _SHARE and c + 1 < len(names):
                    direct = _BANDS.get(names[c + 1], '')
                    if names[c + 1] not in _AMOUNT and not direct:
                        continue
                    candidates = {v for v in bands[c:c + 2] if v}
                    if direct:
                        candidates.add(direct)
                    if len(candidates) == 1:
                        role = candidates.pop()
                        out[h][c:c + 2] = [role + ' PPN', role + ' AMOUNT']
                    else:
                        unresolved.append(c + 1)
                        out[h][c:c + 2] = ['UNRESOLVED BAND SHARE', 'UNRESOLVED BAND VALUE']
                elif name in _BANDS and c + 1 < len(names) and not names[c + 1]:
                    # Named two-column band without a subheader (2023 Q3).
                    # Its second cell is otherwise unlabelled; another band
                    # in the same header proves this is a banded layout.
                    if not has_subrow and sum(v in _BANDS for v in names) >= 2:
                        out[h][c:c + 2] = [_BANDS[name] + ' PPN', _BANDS[name] + ' AMOUNT']
            if unresolved:
                exceptions.append(ExceptionRecord('WARN', 'source_layout_ambiguous', path.name,
                    sheet, h + 1, 'Claims allocation pairs have missing or contradictory band labels '
                    f'at columns {unresolved}; roles left unresolved for source review.'))
            claim = next(c for c, n in enumerate(names) if n in {'CLAIM NO', 'CLAIMS NO', 'CLAIM NUMBER', 'CLAIMS NUMBER'})
            policy = next(c for c, n in enumerate(names) if n in {'POLICY NO', 'POLICY NUMBER'})
            insured = next((c for c, n in enumerate(names) if n in {'INSURED', 'INSURED NAME', 'NAME OF INSURED CLAIMANT', 'NAME OF INSURED'}), None)
            date_cols = [c for c, n in enumerate(names) if n in {'START DATE', 'END DATE', 'PERIOD OF COVER', 'LOSS DATE', 'DATE OF LOSS', 'DATE PAID', 'DATE OF FINAL SETTLEMENT'}]
            money_cols = [c for c, n in enumerate(out[h]) if n in {
                'TOTAL CLAIMS PAID', 'TOTAL AMOUNT PAID', 'TOTAL CLAIM PAID',
                'TOTAL RESERVE AMOUNT', 'GROSS LOSS RESERVE', 'LOSS RESERVE',
                'FINAL ADJUSTED AMOUNT OF CLAIMS', 'RETENTION AMOUNT', 'TREATY AMOUNT', 'FAC AMOUNT'}]
            for i in range(h + 1, end):
                row = rows[i]
                for c in date_cols:
                    if c < len(row) and isinstance(row[c], str):
                        out[i][c] = _MONTH_RE.sub(lambda m: _MONTHS[m[0].upper()], row[c])
                def val(c):
                    return clean_text(row[c]) if c is not None and c < len(row) else ''
                if val(claim) and not _header(row):
                    for c in money_cols:
                        if (c < len(row) and clean_text(row[c]).upper() not in {'', 'NIL', 'N/A', 'NA', '-', '—'}
                                and parse_number(row[c]) is None):
                            exceptions.append(ExceptionRecord('WARN', 'source_layout_ambiguous',
                                path.name, sheet, i + 1,
                                f'{out[h][c]} at source column {c + 1} has nonnumeric value '
                                f'{row[c]!r}; left blank for source review, not guessed.'))
                # A year in a policy column on a numeric block subtotal is
                # metadata, not a policy. Preserve the numeric controls so the
                # shared footer reconciler can still see them.
                year = val(policy)
                if (not val(claim) and not val(insured) and re.fullmatch(r'(19|20)\d{2}(?:\.0)?', year)
                        and any(parse_number(v) is not None for c, v in enumerate(row) if c != policy)
                        and all(not clean_text(v) or parse_number(v) is not None or clean_text(v).upper() in {'NIL', 'N/A', '-'}
                                for c, v in enumerate(row) if c != policy)):
                    out[i][policy] = None
                    exceptions.append(ExceptionRecord('INFO', 'lasaco_jordans_subtotal', path.name,
                        sheet, i + 1, f'Numeric subtotal with year {year} in policy column; '
                        'year cleared from parser identity, financial controls retained.'))
        return out

    def read_source_workbook(self, path):
        """Recover numeric XML values styled as dates in labelled money fields.

        Visibility is applied first, so hidden rows are never restored. The
        source cached number is copied, including zero and negative amounts;
        no value is calculated from a percentage or another transaction.
        """
        from src.domain.cre_cleaner.io.excel import read_source_workbook, _xlsx_sheet_parts
        from zipfile import ZipFile
        from xml.etree import ElementTree as ET
        from openpyxl.utils.cell import coordinate_to_tuple

        sheets, notes = read_source_workbook(path)
        if path.suffix.lower() not in {'.xlsx', '.xlsm'}:
            return sheets, notes
        candidates = {}
        for sn, rows in sheets.items():
            prepared = self.prepare_source_sheet(sn, rows, path=path, exceptions=[])
            headers = [i for i, row in enumerate(prepared) if _header(row)]
            for index, h in enumerate(headers):
                end = headers[index + 1] if index + 1 < len(headers) else len(rows)
                money = [c for c, n in enumerate(prepared[h]) if n in {
                    'TOTAL CLAIMS PAID', 'TOTAL AMOUNT PAID', 'TOTAL CLAIM PAID',
                    'TOTAL RESERVE AMOUNT', 'GROSS LOSS RESERVE', 'LOSS RESERVE',
                    'FINAL ADJUSTED AMOUNT OF CLAIMS', 'SUM INSURED',
                    'RETENTION AMOUNT', 'TREATY AMOUNT', 'FAC AMOUNT', 'RETENTION', 'TREATY', 'FACULTATIVE'}]
                for r in range(h + 1, end):
                    for c in money:
                        if c < len(rows[r]) and (isinstance(rows[r][c], (datetime, date))
                                or rows[r][c] == '#VALUE!'):
                            candidates.setdefault(sn, set()).add((r, c))
        if not candidates:
            return sheets, notes
        ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
        with ZipFile(path) as archive:
            for sn, part, _ in _xlsx_sheet_parts(archive):
                if sn not in candidates:
                    continue
                for cell in ET.fromstring(archive.read(part)).iter(ns + 'c'):
                    r, c = coordinate_to_tuple(cell.get('r'))
                    if (r - 1, c - 1) not in candidates[sn] or cell.get('t', 'n') != 'n':
                        continue
                    value = cell.find(ns + 'v')
                    number = parse_number(value.text) if value is not None else None
                    if number is not None:
                        sheets[sn][r - 1][c - 1] = number
                        notes.append(('INFO', 'lasaco_jordans_numeric_format', sn, r,
                            f'{cell.get("r")}: source numeric value {number} read from XML; '
                            'the monetary column had an incompatible Excel date format.'))
        return sheets, notes
