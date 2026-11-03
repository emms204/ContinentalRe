"""Royal Exchange — first-pass, unverified.

Treaty analysis / bordereaux / reconciliation workbooks per quarter; some
returns are class-specific (e.g. General Accidents) or AGRIC. Discovery is
quarter-token based; sheet typing is left to the parser.
"""
from __future__ import annotations

import re

from typing import Any, List, Optional, Sequence, Tuple

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.adapters.source_layouts import SourceLayoutAdapter, NamedAllocationRules
from src.domain.cre_cleaner.core.class_labels import normalize_class_label, approved_class_labels
from src.domain.cre_cleaner.models import ExceptionRecord
from src.domain.cre_cleaner.core.detect import SheetTypeRules
from src.domain.cre_cleaner.core.map_columns import PremiumLayoutRules, is_share_header
from src.domain.cre_cleaner.core.normalize import format_amount, normalize_header, parse_number, clean_text


class RoyalExchangePremiumRules(NamedAllocationRules):
    """Royal Exchange premium tabs label the retention share 'RTN %'
    (IMPL-20260929-04: moved out of the shared rules; same behaviour)."""
    PPN_EXACT = PremiumLayoutRules.PPN_EXACT | {"RTN%"}
    PPN_PCT_BAND_WORDS = ("RTN", "RETN", "QS") + PremiumLayoutRules.PPN_PCT_BAND_WORDS
    RET_BAND_WORDS = PremiumLayoutRules.RET_BAND_WORDS + ("RTN%", "RETN")

    def band_kind(self, label):
        label = normalize_header(label)
        if re.match(r"^(?:RTN|RETN)(?: |%|$)", label):
            return "ret"
        if re.match(r"^TTY(?: |%|$)", label):
            return "sur"
        if re.match(r"^SUR(?: |$)", label):
            return "sur"
        return super().band_kind(label)


# Outstanding-claims system export (2020–2021 AGRIC): Claim No | Policy No |
# UW Year | Assured Name | Policy From Date | Policy To Date | Loss Date |
# Sum Insured | Amount O/S | Retn % | Retn Amount | Qs % | Qs Amount |
# Sur % | Sur Amount | FAC % | FAC Amount. Quota share is the treaty band;
# the surplus amount is a further treaty layer.
ROYAL_EXCHANGE_CLAIMS_ALIAS_EXTRA = {
    "insured": ["ASSURED NAME", "INSURED"],
    "details": ["LOSS DESC"],
    "period_from": ["POLICY FROM DATE"],
    "period_to": ["POLICY TO DATE"],
    "total_claims": ["AMOUNT O S"],
    "amount_ret": ["RETN AMOUNT"],
    "amount_treaty": ["QS AMOUNT", "SUR AMOUNT"],
}
# 'OS CLAIMS' tabs are outstanding.
ROYAL_EXCHANGE_SHEET_RULES = SheetTypeRules(ost_tokens=frozenset({"OST", "OS"}))
# 'CLM PD' tabs: AMOUNT PAID; reserve exports: OUTSTANDING AMOUNT (not RESERVED AMOUNT).
ROYAL_EXCHANGE_CLAIMS_TOTAL_FALLBACK = {"paid": ("AMOUNT PAID",), "outstanding": ("OUTSTANDING AMOUNT",)}

# Parent classes explicitly confirmed by the user for Royal Exchange DIRECT.
# Keep this vocabulary out of the generic map and the single-class AGRIC adapter.
ROYAL_GENERAL_ACCIDENT_LABELS = (
    'PL', 'PUBLIC LIABILITY', 'WC', 'WORKMEN COMPENSATION',
    "WORKMEN'S COMPENSATION", 'WORKMEN S COMPENSATION', 'WORKMENS COMPENSATION',
    'BURG', 'BURGLARY', 'ALL RISKS', 'ALL RISK', 'FG', 'FIDELITY GUARANTY',
    'FIDELITY GUARANTEE', 'MONEY', 'MONEY INSURANCE', 'GPA',
    'GROUP PERSONAL ACCIDENT', 'PROF IND', 'PROFESSIONAL INDEMNITY',
    'PROFESSIONAL INDEMITY', 'GIT', 'GOODS IN TRANSIT',
)
_ROYAL_GENERAL_ACCIDENT_SHEET = re.compile(
    r'(?<![A-Z0-9])(?:' + '|'.join(
        re.escape(label) for label in sorted(
            {normalize_header(v) for v in ROYAL_GENERAL_ACCIDENT_LABELS},
            key=len, reverse=True,
        )
    ) + r')(?![A-Z0-9])'
)

_BAND_WORDS = ("RETENTION", "TREATY", "SURPLUS", "QUOTA", "FACULTATIVE", "POOL")
# Shares under the band-named columns add up to 100 (or 1.0) per row, within
# this many percentage points, on at least this share of the rows.
_SHARE_TOTAL_PCT = 100.0
_SHARE_TOTAL_TOL_PCT = 1.0
_SHARE_ROWS_AGREEING = 0.8
# An AMOUNT column holds money when some value is larger than any share.
_MONEY_MIN_ABS = _SHARE_TOTAL_PCT


def inline_band_pairs(header: Sequence[Any]) -> List[Tuple[int, int]]:
    """(band column, AMOUNT column) pairs of the 2022+ reserve export:
    ... | Net Retentions | AMOUNT | TREATY 1ST SURPLUS | AMOUNT | ... |
    Facultative | AMOUNT. A band already split by a PPN / AMOUNT sub-row is
    not a pair."""
    pairs = []
    for i in range(1, len(header)):
        if normalize_header(header[i]) != "AMOUNT" or header[i - 1] is None:
            continue
        norm = normalize_header(header[i - 1])
        if any(w in norm for w in _BAND_WORDS) and not is_share_header(norm):
            pairs.append((i - 1, i))
    return pairs


def _cell(row: Sequence[Any], i: int) -> Optional[float]:
    return parse_number(row[i]) if i < len(row) else None


def band_shares_check(
    pairs: Sequence[Tuple[int, int]], data_rows: Sequence[Sequence[Any]],
) -> Tuple[bool, str, int, float]:
    """(relabel?, reason when not, rows with values, sum of the AMOUNT columns).
    Relabel only when every band-column value is a share (0–100 or 0–1)
    adding up to about 100% across the bands and the AMOUNT columns hold
    money."""
    rows = agreeing = 0
    money = 0.0
    big_amount = False
    for r in data_rows:
        shares = [v for v in (_cell(r, b) for b, _a in pairs) if v is not None]
        amounts = [v for v in (_cell(r, a) for _b, a in pairs) if v is not None]
        if not shares and not amounts:
            continue
        rows += 1
        money += sum(amounts)
        big_amount = big_amount or any(abs(v) > _MONEY_MIN_ABS for v in amounts)
        if any(v < 0 or v > _SHARE_TOTAL_PCT for v in shares):
            return False, f"band column holds {max(shares, key=abs):,.2f}, not a share", rows, money
        total = sum(shares)
        scale = 1.0 if total <= 1.0 + _SHARE_TOTAL_TOL_PCT / _SHARE_TOTAL_PCT else _SHARE_TOTAL_PCT
        if abs(total - scale) <= _SHARE_TOTAL_TOL_PCT * scale / _SHARE_TOTAL_PCT:
            agreeing += 1
    if rows == 0:
        return False, "no values under the band / AMOUNT columns", rows, money
    if agreeing < _SHARE_ROWS_AGREEING * rows:
        return False, (f"band shares add up to about 100% on only {agreeing} of {rows} rows"), rows, money
    if not big_amount:
        return False, "AMOUNT columns hold no money (no value above 100)", rows, money
    return True, "", rows, money


def inline_band_amount_header(
    header: Sequence[Any],
    data_rows: Sequence[Sequence[Any]] = (),
    notes: Optional[list] = None,
) -> List[Any]:
    """The band-named column holds the share %, the AMOUNT column after it
    the money: rename them '<band> PPN' / '<band>' — only when the values say
    so (``band_shares_check``); otherwise the header is left as it is. Both
    outcomes move money columns, so both are a WARN in ``notes``."""
    pairs = inline_band_pairs(header)
    if not pairs:
        return list(header)
    labels = ", ".join(str(header[b]).strip() for b, _a in pairs)
    ok, why, rows, money = band_shares_check(pairs, data_rows)
    if not ok:
        if notes is not None:
            notes.append(("WARN", "band_amount_relabel_skipped",
                          f"band columns ({labels}) left as they are: {why}; {rows} rows, "
                          f"{format_amount(money)} in the AMOUNT columns — check the band mapping"))
        return list(header)
    out = list(header)
    for b, a in pairs:
        label = str(out[b]).strip()
        out[b], out[a] = f"{label} PPN", label
    if notes is not None:
        notes.append(("WARN", "band_amount_relabelled",
                      f"band columns ({labels}) hold shares adding up to 100%; the AMOUNT column "
                      f"after each is its money: {rows} rows, {format_amount(money)}"))
    return out


class RoyalExchangeClaimsMixin(SourceLayoutAdapter):
    strict_sheet_class_hints = False
    combine_treaty_layers = True
    premium_alias_extra = {
        'insured': ['ASSURED NAME'],
        'period_from': ['POLICY FROM DATE'], 'period_to': ['POLICY TO DATE'],
    }

    @staticmethod
    def _header(row):
        names = [normalize_header(v) for v in row]
        policy = any(v in names for v in ('POLICY NO', 'POLICY NUMBER'))
        insured = any(v in names for v in ('INSURED', 'NAME OF INSURED', 'ASSURED NAME', 'INSURED NAME', 'CLAIMANT'))
        return policy and insured

    def _prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        """Normalize only header cells; keep physical source row/column positions."""
        result = [list(row) for row in rows]
        header = None
        for n, row in enumerate(result):
            if self._header(row):
                names = [normalize_header(v) for v in row]
                claims = any(v in names for v in ('CLAIM NO', 'CLAIM NUMBER'))
                # A source header, not a transaction or report title.
                replacements = {'UW SI': 'SUM INSURED', 'UW PREM': 'GROSS PREMIUM',
                                'RENTENTION SUM INSURED': 'RETENTION SUM INSURED'}
                if not claims:
                    replacements.update({'ASSURED NAME': 'INSURED',
                                         'POLICY FROM DATE': 'START DATE', 'POLICY TO DATE': 'END DATE'})
                    if 'GROSS PREMIUM' not in names:
                        replacements['OUR SHARE OF G PREM'] = 'GROSS PREMIUM'
                    if 'SUM INSURED' not in names:
                        replacements['OUR SHARE SI'] = 'SUM INSURED'
                row[:] = [replacements.get(v, original) for original, v in zip(row, names)]
                # Explicit band labels plus their SI/premium subrow. Some
                # reports place the band label over SI (PPN is one cell left).
                if not claims and n + 1 < len(result):
                    sub = [normalize_header(v) for v in result[n + 1]]
                    if sum(v in {'PPN', 'SUM INSURED', 'PREMIUM', 'SI'} for v in sub) >= 3:
                        merged = list(row) + [None] * max(0, len(sub) - len(row))
                        for i, label in enumerate(row):
                            norm = normalize_header(label)
                            if not self.premium_layout_rules.band_kind(norm):
                                continue
                            start = i - 1 if i and i < len(sub) and sub[i] in {'SUM INSURED', 'SI'} and sub[i-1] == 'PPN' else i
                            for j in range(start, min(start + 3, len(sub))):
                                if sub[j] in {'PPN', 'SUM INSURED', 'SI', 'PREMIUM'}:
                                    merged[j] = norm + ' ' + sub[j]
                        row[:] = merged
                        result[n + 1] = [None] * len(result[n + 1])
                if not claims:
                    # Flat exports repeat identical SURPLUS %/SI/Prem names.
                    # Give each physical band its own label before detection,
                    # or a later layer can replace an earlier one in the map.
                    share_cols = [i for i, v in enumerate(row)
                                  if is_share_header(normalize_header(v)) and self.premium_layout_rules.band_kind(normalize_header(v))]
                    for ordinal, i in enumerate(share_cols, 1):
                        label = normalize_header(row[i])
                        kind = self.premium_layout_rules.band_kind(label)
                        stem = re.sub(r'\bPPN\b|%', '', label).strip()
                        prefix = ('RETENTION' if kind == 'ret' else 'FACULTATIVE' if kind == 'fac'
                                  else f'TREATY {stem} LAYER {ordinal}')
                        row[i] = prefix + ' PPN'
                        end = share_cols[ordinal] if ordinal < len(share_cols) else len(row)
                        for j in range(i+1, end):
                            norm = normalize_header(row[j])
                            if self.premium_layout_rules.looks_like_si(norm) or re.search(r'\bSI(?:\s+\d+)?$', norm):
                                row[j] = prefix + ' SUM INSURED'
                            elif self.premium_layout_rules.looks_like_prem(norm):
                                row[j] = prefix + ' PREMIUM'
                header = {normalize_header(v): i for i, v in enumerate(row) if normalize_header(v)}
                continue
            if not header:
                continue
            def value(labels):
                i = next((header[v] for v in labels if v in header), None)
                return row[i] if i is not None and i < len(row) else None
            if any(v in header for v in ('CLAIM NO', 'CLAIM NUMBER')):
                policy = clean_text(value(('POLICY NO', 'POLICY NUMBER')))
                claim = clean_text(value(('CLAIM NO', 'CLAIM NUMBER')))
                labels = ('INSURED', 'INSURED NAME', 'ASSURED NAME', 'CLAIMANT')
                years = [parse_number(value((v,))) for v in labels]
                if not policy and not claim and any(v is not None and 1900 <= v <= 2100 and v == int(v) for v in years):
                    result[n] = [None] * len(row)
                    exceptions.append(ExceptionRecord('WARN', 'royal_summary_row_skipped', path.name, sheet, n+1,
                                                      'Year summary without policy/claim identifiers; not a claim'))
                    continue
            for label in ('START DATE', 'END DATE', 'POLICY FROM DATE', 'POLICY TO DATE', 'PERIOD FROM', 'PERIOD TO'):
                i = header.get(label)
                if i is None or i >= len(row):
                    continue
                number = parse_number(row[i])
                if number is not None and 1900 <= number <= 2100 and number == int(number):
                    row[i] = None
                    exceptions.append(ExceptionRecord('WARN', 'royal_year_only_date', path.name, sheet, n+1,
                                                      f'{label} contains year {int(number)}, not a date; left blank'))
        return result

    def read_source_workbook(self, path):
        from collections import Counter
        stat = path.stat()
        book_key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
        cache = getattr(self, '_royal_source_books', {})
        if book_key in cache:
            sheets, notes = cache[book_key]
            return {sn: [list(r) for r in rows] for sn, rows in sheets.items()}, list(notes)
        sheets, notes = super().read_source_workbook(path)
        for sn, rows in sheets.items():
            if (any(self._header(r) and 'GROSS AMOUNT' in [normalize_header(v) for v in r] for r in rows)
                    and any(normalize_header(v) == 'REVIEW' for r in rows for v in r)):
                review_row = next(n for n, r in enumerate(rows, 1) if any(normalize_header(v) == 'REVIEW' for v in r))
                notes.append(('ERROR', 'source_layout_ambiguous', sn, review_row,
                              'Competing original and REVIEW allocation columns: confirm which set to use; no output published'))
                return {}, notes
        exceptions = []
        original_sheets = sheets
        sheets = {sn: self._prepare_source_sheet(sn, rows, path=path, exceptions=exceptions)
                  for sn, rows in sheets.items()}
        # Remove only proven overlaps between a cumulative tab and class tabs.
        # Nonempty debit notes and the entire normalized source payload must
        # match. Preserve multiplicity within each tab and different debit notes.
        owners = {}
        def is_cumulative(name):
            return bool(re.search(r'\b(?:CUM|CUMM|CUMULATIVE)\b', normalize_header(name)))
        # Prefer class-specific evidence when a cumulative copy is identical.
        for sn, rows in sorted(sheets.items(), key=lambda item:is_cumulative(item[0])):
            cumulative = is_cumulative(sn)
            header = None
            occurrences = Counter()
            for n, row in enumerate(rows):
                if self._header(row):
                    header = [normalize_header(v) for v in row]
                    continue
                if not header:
                    continue
                def at(label):
                    i = header.index(label) if label in header else None
                    return clean_text(row[i]) if i is not None and i < len(row) else ''
                claim_id = at('CLAIM NO') or at('CLAIM NUMBER')
                is_claim = any(v in header for v in ('CLAIM NO', 'CLAIM NUMBER'))
                identity = claim_id if is_claim else at('DEBIT NOTE')
                if not identity or not (at('POLICY NO') or at('POLICY NUMBER')):
                    continue
                outstanding = (any('OUTSTANDING' in v or v == 'AMOUNT O S' for v in header)
                               or bool(re.search(r'\b(?:OS|OST|OUTSTANDING)\b', sn, re.I)))
                kind = ('outstanding' if outstanding else 'paid') if is_claim else 'premium'
                # Column order is irrelevant, but extra source fields cannot
                # silently differ. Normalize numbers and exact class vocabulary.
                payload = []
                source_row = original_sheets[sn][n]
                for i in range(max(len(header), len(source_row))):
                    label = header[i] if i < len(header) else ''
                    value = source_row[i] if i < len(source_row) else None
                    if not label:
                        if not clean_text(value):
                            continue
                        label = f'UNLABELLED SOURCE COLUMN {i+1}'
                    # Invalid dates may be blanked for output, but their
                    # original values must still distinguish source records.
                    identifier = label in {'POLICY NO', 'POLICY NUMBER', 'DEBIT NOTE',
                                           'CLAIM NO', 'CLAIM NUMBER', 'CESSION NO'}
                    numeric = None if identifier else parse_number(value)
                    value = numeric if numeric is not None else clean_text(value)
                    if label == 'POLICY CLASS':
                        value = normalize_class_label(value, self.class_map()) or value
                    payload.append((label, value))
                key = (kind, tuple(sorted(payload, key=lambda x:x[0])))
                occurrences[key] += 1
                previous = owners.get(key, [])
                matching = [(owner, count) for owner, is_cum, count in previous
                            if owner != sn and (is_cum or cumulative)]
                if matching and occurrences[key] <= max(count for _, count in matching):
                    rows[n] = [None] * len(row)
                    exceptions.append(ExceptionRecord('WARN', 'royal_cumulative_overlap_skipped', path.name, sn, n+1,
                                                      f'Identical source transaction identifier/values already in {matching[0][0]!r}; not loaded twice'))
            for key, count in occurrences.items():
                owners.setdefault(key, []).append((sn, cumulative, count))
        notes.extend((e.severity, e.reason, e.source_sheet, e.source_row, e.detail) for e in exceptions)
        self._royal_source_books = {**cache, book_key: (sheets, list(notes))}
        return {sn: [list(r) for r in rows] for sn, rows in sheets.items()}, list(notes)

    def map_premium_columns(self, header, group_row, **kwargs):
        cm = super().map_premium_columns(header, group_row, **kwargs)
        cm.combine_treaty_layers = True
        # A short FAC band can supply PPN + premium without SI. Bind its
        # explicit cells; never borrow SI from another band's columns.
        names = [normalize_header(v) for v in header]
        for attr, predicate in [('fac_ppn', is_share_header),
                                ('fac_si', self.premium_layout_rules.looks_like_si),
                                ('fac_prem', self.premium_layout_rules.looks_like_prem)]:
            cols = [i for i, v in enumerate(names) if self.premium_layout_rules.band_kind(v) == 'fac' and predicate(v)]
            if len(cols) <= 1:
                setattr(cm, attr, cols[0] if cols else None)
        return cm

    def resolve_premium_class(self, *, sheet_hint, section, raw, **context):
        cmap = self.class_map()
        approved = approved_class_labels(cmap)
        mapped = normalize_class_label(raw, cmap)
        if mapped in approved:
            return mapped, 'column', raw
        # Mixed/cumulative tabs have no authoritative sheet class. Preserve an
        # unresolved Policy Class (e.g. Marine), so review targets those rows
        # rather than offering the entire mixed sheet as a class alias.
        if (clean_text(raw)
                and normalize_class_label(section, cmap) not in approved
                and normalize_class_label(sheet_hint, cmap) not in approved):
            return clean_text(raw), 'column', raw
        # Unapproved source labels still require an explicit parent decision.
        return section or sheet_hint or raw, 'banner' if section else 'sheet', raw or section or sheet_hint

    def map_claims_columns(self, header, aliases, sheet_type=''):
        cm = super().map_claims_columns(header, aliases, sheet_type)
        amounts, shares = [], []
        for i, value in enumerate(header):
            norm = normalize_header(value)
            kind = self.premium_layout_rules.band_kind(norm)
            if kind == 'sur':
                if is_share_header(norm):
                    shares.append(i)
                elif 'TOTAL' not in norm:
                    amounts.append((i, norm))
            if not is_share_header(norm) and kind in {'ret', 'fac'}:
                cm.mapping['amount_' + kind] = i
            if is_share_header(norm) and kind in {'ret', 'fac'}:
                cm.mapping['ppn_' + kind] = i
        aggregate = next((i for i, v in enumerate(header) if normalize_header(v) in
                          {'TOTAL RECOVERY', 'TOTAL TREATY', 'TOTAL TREATY AMOUNT'}), None)
        if aggregate is not None:
            cm.mapping['amount_treaty'] = aggregate
        elif amounts:
            cm.mapping['amount_treaty'] = amounts[0][0]
            cm.extra_amount_columns = amounts[1:]
            cm.treaty_ppn_columns = shares
        cm.combine_treaty_layers = True
        return cm

    def check_claims_row(self, claim, *, row, cmap, exceptions, path, sheet, excel_row):
        for field in ('ppn_ret', 'ppn_treaty', 'ppn_fac'):
            value = getattr(claim, field)
            if value is not None and abs(value) > 100:
                setattr(claim, field, None)
                exceptions.append(ExceptionRecord(
                    'WARN', 'royal_invalid_source_share', path.name, sheet, excel_row,
                    f'{field} source value {value} exceeds 100; share left blank, explicit AMOUNT preserved'))

    claims_alias_extra = ROYAL_EXCHANGE_CLAIMS_ALIAS_EXTRA
    claims_total_fallback = ROYAL_EXCHANGE_CLAIMS_TOTAL_FALLBACK
    sheet_type_rules = ROYAL_EXCHANGE_SHEET_RULES

    def claims_header_cells(
        self, header: Sequence[Any], band_row: Optional[Sequence[Any]],
        data_rows: Sequence[Sequence[Any]] = (), notes: Optional[list] = None,
    ) -> List[Any]:
        # Summary/footer cells are not evidence about transaction-column units.
        # They can contain money under a percentage column and must not veto
        # the share/AMOUNT mapping for every real claim in the block.
        identity_cols = [i for i, value in enumerate(header)
                         if normalize_header(value) in {'POLICY NO', 'POLICY NUMBER', 'CLAIM NO', 'CLAIM NUMBER'}]
        evidence_rows = [row for row in data_rows if any(i < len(row) and clean_text(row[i]) for i in identity_cols)] if identity_cols else data_rows
        result = super().claims_header_cells(
            inline_band_amount_header(header, evidence_rows, notes), band_row, data_rows, notes)
        for i in range(len(header) - 1):
            norm = normalize_header(header[i])
            base = re.sub(r'\s+PPN(?:\s*%)?$', '', norm)
            if (self.premium_layout_rules.band_kind(norm)
                    and normalize_header(header[i+1]) in {'AMOUNT', base + ' AMOUNT'}):
                result[i], result[i+1] = base + ' PPN', base + ' AMOUNT'
        for i in range(len(result) - 1):
            norm = normalize_header(result[i])
            if is_share_header(norm) and self.premium_layout_rules.band_kind(norm) and normalize_header(result[i+1]) == 'AMOUNT':
                result[i+1] = re.sub(r'\s+PPN(?:\s*%)?$', '', norm) + ' AMOUNT'
        return result


class RoyalExchangeAdapter(RoyalExchangeClaimsMixin, QuarterlyWorkbookAdapter):
    premium_layout_rules = RoyalExchangePremiumRules()
    cedant = "ROYAL EXCHANGE"
    broker = "DIRECT"
    class_label_extra = {label: 'General Accident' for label in ROYAL_GENERAL_ACCIDENT_LABELS}
    # Exact source CLASS values; decorated sheet names use the bounded hook below.
    class_label_exact_only = frozenset(ROYAL_GENERAL_ACCIDENT_LABELS)

    def sheet_class_hint(self, sheet, class_hint):
        if normalize_class_label(class_hint, self.class_map()) in approved_class_labels(self.class_map()):
            return normalize_class_label(class_hint, self.class_map())
        if _ROYAL_GENERAL_ACCIDENT_SHEET.search(normalize_header(sheet)):
            return 'General Accident'
        return class_hint

    verified = False
    status_note = (
        "Royal Exchange: quarterly treaty analysis/bordereaux workbooks; "
        "not checked against Bisola gold"
    )


class RoyalExchangeAgricAdapter(RoyalExchangeClaimsMixin, QuarterlyWorkbookAdapter):
    premium_layout_rules = RoyalExchangePremiumRules()
    cedant = "ROYAL EXCHANGE"
    broker = "AGRIC"
    settings = {"single_class": "Agriculture"}
    verified = False
    status_note = "Royal Exchange Agric: quarterly returns; unverified"
