"""JOMOLA claims block validation. Never turn correction ledgers into payments."""
from datetime import date, datetime
import json

from src.domain.cre_cleaner.core.class_labels import normalize_class_label
from src.domain.cre_cleaner.core.normalize import clean_text, normalize_header, parse_date, parse_number
from src.domain.cre_cleaner.models import ExceptionRecord


def prepare_claims_blocks(rows, *, path, sheet, exceptions):
    result = [list(row) for row in rows]
    header = None
    corrections = False
    for index, row in enumerate(rows):
        labels = [normalize_header(v) for v in row]
        text = ' '.join(clean_text(v).upper() for v in row if clean_text(v))
        if {'DATE OF LOSS', 'NAME OF INSURED CLAIMANT', 'CLAIM NO', 'POLICY NO'}.issubset(labels):
            header = {label: i for i, label in enumerate(labels) if label}
            corrections = False
            continue
        if header is not None and ('WRONG CESSION' in text or 'EXCESS RECOVERED' in text):
            corrections = True
        if corrections:
            result[index] = [None] * len(row)
            def cell(i):
                return row[i] if i < len(row) else None
            # The marked wide correction ledger contains class/subclass,
            # insured/claim/policy, cover dates and an explicit signed recovery.
            if clean_text(cell(6)) and clean_text(cell(8)):
                parent = normalize_class_label(clean_text(cell(3)))
                adjustment = parse_number(cell(44))
                valid = (parent not in {'', 'Other'} and isinstance(cell(6), str)
                         and not isinstance(cell(7), (date, datetime))
                         and bool(clean_text(cell(7))) and parse_date(cell(10)) is not None
                         and parse_date(cell(11)) is not None and adjustment is not None)
                valid = valid and (adjustment >= 0 if 'CORRECT CESSION' in text else adjustment <= 0)
                if valid:
                    item = dict(source_row=index + 1, file=path.name, sheet=sheet,
                                role='correct_cession' if 'CORRECT CESSION' in text else 'prior_cession_reversal',
                                **{'class': parent}, insured=clean_text(cell(6)),
                                claim_no=clean_text(cell(7)), policy_no=clean_text(cell(8)),
                                recovery_adjustment=adjustment)
                    exceptions.append(ExceptionRecord('WARN', 'heirs_correction_record', path.name,
                        sheet, index + 1, json.dumps(item)))
            if any(clean_text(v) for v in row):
                exceptions.append(ExceptionRecord('WARN', 'heirs_claims_correction_review', path.name,
                    sheet, index + 1, 'Correction ledger requires adjustment review; excluded from ordinary paid claims. Original signed values remain in correction diagnostics.'))
            continue
        if header is None:
            continue
        if not {'PERIOD OF COVER FROM', 'PERIOD OF COVER TO', 'TOTAL AMOUNT PAID'}.issubset(header):
            continue  # Legacy and merged-header layouts have their own validators.
        def value(label):
            i = header.get(label)
            return row[i] if i is not None and i < len(row) else None
        # Detect a wide/shifted transaction by an actual loss date. Banners and
        # printed totals do not look like transactions and retain their roles.
        loss = value('DATE OF LOSS')
        if not isinstance(loss, (date, datetime)):
            continue
        insured, claim, policy = (value(k) for k in ('NAME OF INSURED CLAIMANT', 'CLAIM NO', 'POLICY NO'))
        valid = (isinstance(insured, str) and bool(clean_text(insured))
                 and not isinstance(claim, (date, datetime)) and bool(clean_text(claim))
                 and not isinstance(policy, (date, datetime)) and bool(clean_text(policy)))
        for label in ('PERIOD OF COVER FROM', 'PERIOD OF COVER TO'):
            if label in header and parse_date(value(label)) is None:
                valid = False
        if {'CLAIM PAID', 'ADJUSTER FEE PAID', 'TOTAL AMOUNT PAID'}.issubset(header):
            originals = [value(label) for label in ('CLAIM PAID', 'ADJUSTER FEE PAID', 'TOTAL AMOUNT PAID')]
            paid, fees, total = map(parse_number, originals)
            comparable = all(not isinstance(v, str) or not any(c.isalpha() for c in v) for v in originals if v is not None)
            if not comparable:
                exceptions.append(ExceptionRecord('INFO', 'heirs_claims_mixed_currency_basis', path.name,
                    sheet, index + 1, 'Explicit TOTAL AMOUNT PAID retained; currency-labelled component cannot be reconciled without an exchange-rate basis.'))
            if total is None or (comparable and abs(total - (paid or 0) - (fees or 0)) > .02):
                valid = False
        if not valid:
            result[index] = [None] * len(row)
            exceptions.append(ExceptionRecord('WARN', 'heirs_claims_layout_unresolved', path.name,
                sheet, index + 1, 'Claims row does not fit the active header: identity/date positions or paid/fee/total relationship failed; not loaded using stale column positions.'))
    return result
