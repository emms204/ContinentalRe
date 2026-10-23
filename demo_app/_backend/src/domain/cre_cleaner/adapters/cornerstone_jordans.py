"""JORDANS source evidence, separate from SCIB/HILAL vocabulary."""
import re
from math import isclose

from src.domain.cre_cleaner.core.normalize import clean_text, normalize_header, parse_date, parse_number
from src.domain.cre_cleaner.core.class_labels import normalize_class_label, is_unresolved_class
from src.domain.cre_cleaner.models import ExceptionRecord


HEADER = ['CHANNEL', 'POLICY CLASS', 'CESSION NUMBER', 'POLICY NO.', 'NAME OF INSURED',
          'CHANNEL', 'SUB CHANNEL', 'TOTAL SUM INSURED', 'SOURCE MPL AMOUNT', 'RATE',
          'GROSS PREMIUM', 'FROM', 'TO', 'RETENTION PPN', 'RETENTION SUM INSURED',
          'RETENTION PREMIUM', 'SURPLUS PPN', 'SURPLUS SUM INSURED', 'SURPLUS PREMIUM',
          'FACULTATIVE PPN', 'FACULTATIVE SUM INSURED', 'FACULTATIVE PREMIUM']


class JordansEvidence:
    premium_header_scan_all = True

    def _cover_class(self, text):
        if is_unresolved_class(text):
            return 'MARINE'
        key = normalize_header(text)
        # These exact product labels also occur under explicit BOND/BONDS
        # subclasses in this partner's source returns. Never global aliases.
        if key in {'INDEMNITY FOR LOSS OF SHARE CERTIFICATE', 'FEDERAL GOVERNMENT GUARANTEE',
                   'CUSTOM BOND', 'CREDIT BOND'}:
            return 'Bond'
        if key == 'GOODS IN TRANSIT ANNUAL POLICY':
            return 'General Accident'
        value = normalize_class_label(text, self.class_map())
        return value if value not in {'', 'Other'} else ''

    @staticmethod
    def _transaction(row):
        return (len(row) > 7 and bool(re.fullmatch(r'(?:DN|CN)\d+-\d+', clean_text(row[2])))
                and bool(clean_text(row[3])) and bool(clean_text(row[4])))

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        result = super().prepare_source_sheet(sheet, rows, path=path, exceptions=exceptions)
        result = [list(r) for r in result]
        evidence = getattr(self, '_jordans_evidence', {})
        parent = subclass = ''
        prefix = (str(path.resolve()), sheet)
        for key in [k for k in evidence if k[:2] == prefix]:
            del evidence[key]
        for i, row in enumerate(rows):
            filled = [clean_text(v) for v in row if clean_text(v)]
            if filled:
                tag = normalize_header(filled[0])
                if tag == 'CLASS' and len(filled) == 2:
                    parent, subclass = filled[1], ''
                elif tag == 'SUBCLASS' and len(filled) == 2:
                    subclass = filled[1]
                elif len(filled) == 1 and normalize_header(filled[0]) in {'BOND', 'BONDS', 'BOND TREATY'}:
                    parent, subclass = 'Bond', ''
                elif tag == 'BOND TREATY' and all(parse_number(v) is not None for v in filled[1:]):
                    parent, subclass = 'Bond', ''
            if self._transaction(row):
                evidence[(*prefix, i + 1)] = dict(section_label=parent, subclass_label=subclass,
                    row_class=clean_text(row[1]), product_class=self._cover_class(row[1]))
        self._jordans_evidence = evidence
        has_header = any({'POLICY NO', 'NAME OF INSURED'} <= {normalize_header(v) for v in r} for r in rows)
        candidates = [(i, r) for i, r in enumerate(rows) if self._transaction(r)]
        if candidates and not has_header and 'CLAIM' not in sheet.upper():
            self._restore_header(result, candidates, path, sheet, exceptions)
        return result

    def _restore_header(self, rows, candidates, path, sheet, exceptions):
        first = candidates[0][0]
        valid = first > 0 and not any(clean_text(v) for v in rows[first - 1])
        for i, row in candidates:
            def number(col):
                return parse_number(row[col]) if col < len(row) else None
            gross = number(10)
            valid = valid and number(7) is not None and gross is not None
            valid = valid and len(row) > 12 and all(parse_date(row[j]) is not None for j in (11,12))
            amounts = []
            for start in (13,16,19):
                pct, premium = number(start), number(start + 2)
                amounts.append(premium or 0)
                if pct is None:
                    valid = valid and premium in (None, 0)
                else:
                    valid = valid and 0 <= pct <= 100 and premium is not None and gross is not None
                    if premium is not None and gross is not None:
                        valid = valid and isclose(premium, gross * pct / 100, rel_tol=1e-8, abs_tol=.03)
            valid = valid and gross is not None and isclose(sum(amounts), gross, rel_tol=1e-8, abs_tol=.03)
        # A numeric source footer must independently control the gross and
        # every allocation premium it supplies. No control is manufactured.
        controls = [r for r in rows[candidates[-1][0] + 1:] if len(r) > 10
                    and not any(clean_text(v) for v in r[:7]) and parse_number(r[10]) is not None]
        valid = valid and len(controls) == 1
        if len(controls) == 1:
            for col in (10,15,18,21):
                control = parse_number(controls[0][col]) if col < len(controls[0]) else None
                if control is not None:
                    total = sum(parse_number(r[col]) or 0 for _,r in candidates if col < len(r))
                    valid = valid and isclose(total, control, rel_tol=1e-8, abs_tol=.03)
        if not valid:
            exceptions.append(ExceptionRecord('ERROR', 'source_layout_ambiguous', path.name, sheet,
                first + 1, 'Headerless JORDANS premium layout could not be verified from identities, dates, '
                'premium share arithmetic and printed footer controls; no positional extraction applied'))
            return
        rows[first - 1] = list(HEADER)
        exceptions.append(ExceptionRecord('INFO', 'jordans_header_restored', path.name, sheet, first,
            'Missing premium headers restored in the preceding blank parser row; DN/CN identities, '
            'cover dates, signed premium allocations and source footer controls verified; source cells unchanged'))

    def check_premium_row(self, prow, *, exceptions, path, sheet, **context):
        super().check_premium_row(prow, exceptions=exceptions, path=path, sheet=sheet, **context)
        e = getattr(self, '_jordans_evidence', {}).get((str(path.resolve()), sheet, prow.audit.source_row))
        if e is None:
            return
        e = dict(e)
        parent = normalize_class_label(e['section_label'], self.class_map())
        sub = normalize_class_label(e['subclass_label'], self.class_map())
        parent = parent if parent not in {'', 'Other'} else ''
        sub = sub if sub not in {'', 'Other'} else ''
        row_class = e['product_class']
        # Broad GA + explicit BOND subclass is resolved by the narrower,
        # independently agreeing record cover. A Fire/Bond disagreement is not.
        narrow = parent == 'General Accident' and sub == row_class == 'Bond'
        declared = sub or parent
        conflict = bool((parent and sub and parent != sub and not narrow)
                        or declared and row_class and declared != row_class)
        e.update(section_class=declared or parent, candidates=sorted({v for v in (parent,sub,row_class) if v}))
        if conflict:
            prow.audit.class_hint = 'CLASS CONFLICT'
            prow.audit.class_source = 'conflict'
            e['rule'] = 'conflicting_source_classes'
            exceptions.append(ExceptionRecord('WARN', 'jordans_source_class_conflict', path.name,
                sheet, prow.audit.source_row, f"Heading {e['section_label']!r}, subclass {e['subclass_label']!r} "
                f"and record cover {e['row_class']!r} disagree; choose a class for this source record"))
        else:
            label = row_class or declared
            prow.audit.class_hint = label or e['row_class']
            prow.audit.class_source = 'column' if row_class else 'banner'
            e['rule'] = 'explicit_subclass_and_cover' if narrow else 'source_cover_and_heading'
        prow.audit.class_label_raw = e['row_class']
        prow.audit.class_resolution = e
