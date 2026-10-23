"""Cornerstone SCIB, Jordans bond and HILAL takaful returns."""

from src.domain.cre_cleaner.adapters.source_layouts import (
    SourceLayoutAdapter,
    NamedAllocationRules,
    qualify_bands,
)
from src.domain.cre_cleaner.core.normalize import normalize_header
from src.domain.cre_cleaner.adapters.cornerstone_jordans import JordansEvidence


class CornerstoneScibAdapter(SourceLayoutAdapter):
    cedant = "CORNERSTONE"
    broker = "SCIB"
    class_label_extra = {
        "CONSEQUNTIAL LOSS": "Fire",
        "CONSEQUENTIAL LOSS": "Fire",
        "BUSINESS INTERRUPTION": "Fire",
        "COMPUTER ALL RISK": "Engineering",
        "COMPUTER / EEI ALL RISK": "Engineering",
        "COMPUTER/EEI ALL RISK": "Engineering",
        "COMP/EEI": "Engineering",
        "ERECTION ALL RISK (EAR) INSURANCE": "Engineering",
        "MACHINERY BREAKDOWN": "Engineering",
        "PLANT ALL RISK INSURANCE": "Engineering",
        "PLANT ALL RISK": "Engineering",
        "CONTRACTORS ALL RISKS (CAR) INSURANCE": "Engineering",
        "PAR": "Engineering",
        "MBD": "Engineering",
        "PUBLIC LIABILITY INSURANCE": "General Accident",
        "PUBLIC LIABILITY": "General Accident",
        "PRODUCT LIABILITY": "General Accident",
        "EMPLOYEE’S COMPENSATION/EMPLOYERS’ LIABILITY": "General Accident",
        "FSP": "Fire",
        "ENG": "Engineering",
        "MHC": "Marine Cargo",
        "MH": "Marine Hull",
    }
    class_label_exact_only = {"PAR", "MBD", "FSP", "MHC", "MH", "ENG", "COMP/EEI"}
    claims_alias_extra = {
        "claim_no": ["GIBS CLAIM NO"],
        "period": ["PERIOD OF INS"],
        "total_claims": ["AMOUNT PAID", "CLAIM PAID", "OUR SHARE"],
        "amount_ret": ["RETAINED"],
        "details": ["DETAILS OF LOSS"],
        "ppn_ret": ["PPN RET"],
        "ppn_treaty": ["PPN TREATY"],
        "ppn_fac": ["PPN FAC"],
    }
    claims_total_fallback = {
        "paid": ("AMOUNT PAID", "CLAIM PAID", "OUR SHARE"),
        "outstanding": ("RESERVE", "OUTSTANDING AMOUNT"),
    }

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        from src.domain.cre_cleaner.models import ExceptionRecord

        result = list(rows)
        for i, row in enumerate(rows):
            filled = [
                (j, v) for j, v in enumerate(row) if v is not None and str(v).strip()
            ]
            if (
                len(filled) == 1
                and str(filled[0][1]).strip().upper()
                == "FIRE, CONSEQUENTIAL LOSS & HOUSEHOLDER INSURANCE"
            ):
                result[i] = list(row)
                result[i][filled[0][0]] = "CLASS: FIRE"
                exceptions.append(
                    ExceptionRecord(
                        "INFO",
                        "cornerstone_class_banner_normalized",
                        path.name,
                        sheet,
                        i + 1,
                        "Explicit FIRE, CONSEQUENTIAL LOSS & HOUSEHOLDER INSURANCE banner normalized as Fire; source row preserved",
                    )
                )
        return result

    def premium_header_cells(self, raw_rows, header_at, header, group_row):
        groups = [normalize_header(g) for g in (group_row or [])]
        groups = [
            (
                g.replace(" ", "")
                if g.replace(" ", "") in {"RETENTION", "SURPLUS", "FACULTATIVE"}
                else g
            )
            for g in groups
        ]
        h = [normalize_header(x) for x in header]
        if (self.broker == 'SCIB' and len(h) >= 11 and not h[1]
                and h[2:5] == ['NUMBER', 'POLICY NO', 'NAME OF INSURED']
                and h[5:8] == ['CHANNEL', 'SUB CHANNEL', 'INSURED']
                and len(groups) > 2 and groups[2] == 'CESSION'):
            import re
            from src.domain.cre_cleaner.core.class_labels import normalize_class_label, is_unresolved_class
            # Column B is a cover description in this debit/credit layout,
            # not the adjacent cession number. Validate structure rather than
            # requiring every label to be known: unknown covers need review.
            samples = []
            for row in raw_rows[header_at + 1:]:
                labels = [normalize_header(v) for v in row]
                if {'POLICY NO', 'NAME OF INSURED'} <= set(labels):
                    break
                if (len(row) > 4 and re.fullmatch(r'(?:DN|CN)\d+-\d+', str(row[2]).strip())
                        and row[3] and row[4]):
                    samples.append(row[1])
            if (len(samples) >= 2 and all(isinstance(v, str) and v.strip()
                                          and any(c.isalpha() for c in v) for v in samples)
                    and any(normalize_class_label(v, self.class_map()) not in {'', 'Other'}
                            or is_unresolved_class(v) for v in samples)):
                h[1] = 'CLASS'
        for i, g in enumerate(groups[: len(h)]):
            if g == "TOTAL SUM" and h[i] == "INSURED":
                h[i] = "TOTAL SUM INSURED"
            if g == "GROSS" and h[i] == "PREMIUM":
                h[i] = "GROSS PREMIUM"
        return qualify_bands(h, groups, self.premium_layout_rules), groups

    def claims_exclude(self, field, norm):
        return super().claims_exclude(field, norm) or (
            norm == "OUR SHARE"
            and field in {"amount_ret", "amount_treaty", "amount_fac"}
        )


class CornerstoneJordansAdapter(JordansEvidence, CornerstoneScibAdapter):
    broker = "JORDANS"
    settings = {}


class HilalPremiumRules(NamedAllocationRules):
    def band_kind(self, label):
        if normalize_header(label).startswith("TREATY"):
            return "sur"
        return super().band_kind(label)

    def looks_like_prem(self, norm):
        return (
            norm == "CONTRIBUTION"
            or norm.endswith(" CONTRIBUTION")
            or super().looks_like_prem(norm)
        )


class CornerstoneHilalAdapter(CornerstoneScibAdapter):
    """Takaful participant/plan identities and mixed-class source returns."""

    broker = "HILAL"
    premium_header_scan_all = True
    claims_header_scan_all = True
    premium_layout_rules = HilalPremiumRules()
    premium_alias_extra = {
        "class": ["COVERS", "POLICY TYPE", "CLASS OF BUSINESS", "CLASS"],
        "insured": ["NAME"],
        "sum_insured": ["OUR SHARE SUM INSURED", "SUM ASSURED"],
        "gross_premium": ["OUR SHARE GROSS PREMIUM", "GROSS PREMIUM"],
        "period_from": ["ISSUE DATE"],
        "period_to": ["MATURITY DATE"],
    }
    claims_alias_extra = {
        **CornerstoneScibAdapter.claims_alias_extra,
        "insured": ["PARTICIPANT"],
        "policy_no": ["PLAN NO", "PLAN NUMBER"],
        "period_from": ["COMMENCEMENT DATE DAY MTH YEAR"],
        "period_to": ["EXIT DATE DAY MTH YEAR"],
        "total_claims": ["TOTAL CLAIM PAID", "TOTAL PAID AMOUNT"],
        "ppn_ret": ["RETENTION PPN"],
        "ppn_treaty": ["RECOVERY PPN"],
        "amount_treaty": ["RECOVERY AMOUNT", "TREATY"],
    }
    class_label_extra = {
        **CornerstoneScibAdapter.class_label_extra,
        "GIT": "General Accident",
        "G I T": "General Accident",
        "GFSGPA": "General Accident",
        "GPA": "General Accident",
        "MARINE CLAUSE A": "Marine Cargo",
        "MARINE SINGLE TRANSIT": "Marine Cargo",
        "ICC A": "Marine Cargo",
        '"ICC A"': "Marine Cargo",
        "ICC A'": "Marine Cargo",
        "GROUP LIFE": "Life",
        "CREDIT LIFE": "Life",
        "GOODS IN TRANSIT": "General Accident",
        "FIDELITY GUARANTEE": "General Accident",
        "PROFESSIONAL INDEMNITY": "General Accident",
        "MONEY": "General Accident",
        "MONEY PLAN": "General Accident",
        "BURGLARY": "General Accident",
        "CONTRACTORS ALL RISK": "Engineering",
        "INDUSTRIAL ALL RISK": "Fire",
    }
    class_label_exact_only = CornerstoneScibAdapter.class_label_exact_only | {
        "GIT",
        "G I T",
        "GFSGPA",
        "GPA",
        "GROUP LIFE",
        "CREDIT LIFE",
        "MONEY PLAN",
        "ICC A", '"ICC A"', "ICC A'",
    }

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        from src.domain.cre_cleaner.models import ExceptionRecord

        result = list(rows)
        for i, row in enumerate(rows):
            # A source class title may share a row with allocation band titles.
            # Only this complete, non-transaction signature is a section banner.
            filled = [(j, v) for j, v in enumerate(row) if v is not None and str(v).strip()]
            if (filled and filled[0][0] == 0
                    and normalize_header(filled[0][1]) in {'FIRE', 'ENGINEERING', 'GENERAL ACCIDENT', 'BOND', 'MARINE', 'MARINE CARGO', 'MARINE HULL'}
                    and len(filled) > 1
                    and all(normalize_header(v).replace(' ', '') in
                            {'RETENTION', 'SURPLUS', 'FACULTATIVE'} for _, v in filled[1:])):
                result[i] = [row[0]] + [None] * (len(row) - 1)
                exceptions.append(ExceptionRecord('INFO', 'hilal_section_banner', path.name,
                    sheet, i + 1, f'Explicit {row[0]} section beside allocation labels; source positions retained'))
                continue
            # Complete labels identify a header; data and physical row/column
            # positions are never moved to accommodate a different block.
            values = {str(v).strip().upper() for v in row if isinstance(v, str)}
            policy = bool(values & {"POLICY NO", "POLICY NO.", "POLICY NUMBER"})
            claim = bool(values & {"CLAIM NO", "CLAIM NO.", "CLAIM NUMBER"})
            plan = bool(values & {"PLAN NO", "PLAN NO.", "PLAN NUMBER"})
            if claim and plan and "PARTICIPANT" in values:
                h = [normalize_header(v) for v in row]
                h = [
                    (
                        "POLICY NO"
                        if v in {"PLAN NO", "PLAN NUMBER"}
                        else "INSURED NAME" if v == "PARTICIPANT" else v
                    )
                    for v in h
                ]
            elif policy and values & {
                "NAME",
                "ASSURED",
                "INSURED NAME",
                "NAME OF INSURED",
            }:
                h = [normalize_header(v) for v in row]
                for j, value in enumerate(h):
                    if value in {"NAME", "ASSURED"}:
                        h[j] = "NAME OF INSURED"
                    elif value == "PNN":
                        h[j] = "PPN"
                    elif value in {"SUM ASSURED", "SUM COVER", "SUM COVERED"}:
                        h[j] = "SUM INSURED"
                    elif value == "CONTRIBUTION":
                        h[j] = "PREMIUM"
                    elif value.replace("%", "") == "SUM COVERED HILAL 50 SHARE":
                        h[j] = "OUR SHARE SUM INSURED"
                    elif value.replace("%", "") == "CONTRIBUTION HILAL 50 SHARE":
                        h[j] = "OUR SHARE GROSS PREMIUM"
                if "GROSS PREMIUM" not in h and "OUR SHARE GROSS PREMIUM" not in h:
                    first_ppn = next((j for j, v in enumerate(h) if v == "PPN"), len(h))
                    gross = next(
                        (j for j, v in enumerate(h[:first_ppn]) if v == "PREMIUM"), None
                    )
                    if gross is not None:
                        h[gross] = "GROSS PREMIUM"
                gross = next((j for j, v in enumerate(h) if v == "GROSS PREMIUM"), None)
                if gross is not None and gross and h[gross - 1] == "SUM INSURED":
                    h[gross - 1] = "TOTAL SUM INSURED"
            else:
                continue
            if list(row) != h:
                result[i] = h
                exceptions.append(
                    ExceptionRecord(
                        "INFO",
                        "hilal_header_normalized",
                        path.name,
                        sheet,
                        i + 1,
                        "Takaful header labels normalized; source values and locations preserved",
                    )
                )
        result = self._claims_parent_evidence(result)
        return self._claims_block_evidence(result, path=path, sheet=sheet, exceptions=exceptions)

    def _claims_parent_evidence(self, rows):
        """Bind a standalone parent heading to the immediately following header.

        A new header requires a fresh heading. Never carry a prior section
        through an unheaded block or derive Life from a subclass abbreviation.
        Typed parser evidence cannot be impersonated by an Excel cell string.
        """
        from src.domain.cre_cleaner.core.normalize import clean_text
        from src.domain.cre_cleaner.core.class_labels import normalize_class_label

        identity = {'CLASS', 'POLICY NO', 'CLAIM NO', 'INSURED NAME'}
        headers = [(i, [normalize_header(v) for v in row]) for i, row in enumerate(rows)
                   if identity <= {normalize_header(v) for v in row}]
        if not headers:
            return rows
        width = max((len(row) for row in rows), default=0)
        prepared = [list(row) for row in rows]
        parents = {'LIFE', 'FIRE', 'GENERAL ACCIDENT', 'ENGINEERING', 'BOND',
                   'MARINE CARGO', 'MARINE HULL', 'MOTOR', 'AGRICULTURE', 'AVIATION'}
        for block, (header_at, labels) in enumerate(headers):
            lower = headers[block - 1][0] if block else -1
            parent = None
            for i in range(header_at - 1, lower, -1):
                filled = [clean_text(v) for v in rows[i] if clean_text(v)]
                if not filled:
                    continue
                if len(filled) == 1 and normalize_header(filled[0]) in parents:
                    parent = dict(rule='source_section_parent', source_label=filled[0],
                        class_name=normalize_class_label(filled[0], self.class_map()), parent_row=i + 1)
                break  # First nonblank row is authoritative, never search past transactions/totals.
            if parent is None:
                continue
            end = headers[block + 1][0] if block + 1 < len(headers) else len(rows)
            prepared[header_at].extend([None] * (width + 1 - len(prepared[header_at])))
            prepared[header_at][width] = 'HILAL CLAIMS PARENT EVIDENCE'
            identity_cols = [labels.index(v) for v in ('POLICY NO', 'CLAIM NO', 'INSURED NAME')]
            for i in range(header_at + 1, end):
                filled = [normalize_header(v) for v in rows[i] if clean_text(v)]
                complete = all(col < len(rows[i]) and clean_text(rows[i][col]) for col in identity_cols)
                if (len(filled) == 1 and filled[0] in parents
                        or not complete and any(v in {'TOTAL', 'GRAND TOTAL', 'SUBTOTAL'} for v in filled)):
                    break
                if complete:
                    prepared[i].extend([None] * (width + 1 - len(prepared[i])))
                    prepared[i][width] = dict(parent)
        return prepared

    def _claims_block_evidence(self, rows, *, path, sheet, exceptions):
        """Bind ICL only to a closing CREDIT LIFE label in its own claims block.

        Append parser-only evidence; original class codes, money cells and
        physical row numbers remain unchanged. Never register an ICL alias.
        """
        from src.domain.cre_cleaner.core.normalize import clean_text, parse_number
        from src.domain.cre_cleaner.models import ExceptionRecord

        identity = {'CLASS', 'POLICY NO', 'CLAIM NO', 'INSURED NAME'}
        headers = [(i, [normalize_header(v) for v in row]) for i, row in enumerate(rows)
                   if identity <= {normalize_header(v) for v in row}]
        width = max((len(row) for row in rows), default=0)
        prepared = [list(row) for row in rows]
        for block, (header_at, labels) in enumerate(headers):
            if not {'RECOVERY AMOUNT', 'TOTAL CLAIM PAID'} <= set(labels):
                continue
            end = headers[block + 1][0] if block + 1 < len(headers) else len(rows)
            cls, policy, claim, insured, recovery = [labels.index(v) for v in
                ('CLASS', 'POLICY NO', 'CLAIM NO', 'INSURED NAME', 'RECOVERY AMOUNT')]
            def value(row, col):
                return row[col] if col < len(row) else None
            footers = [i for i in range(header_at + 1, end)
                       if normalize_header(value(rows[i], claim)) == 'CREDIT LIFE'
                       and parse_number(value(rows[i], recovery)) is not None
                       and all(not clean_text(v) for j, v in enumerate(rows[i]) if j not in {claim, recovery})]
            if len(footers) != 1:
                continue
            footer = footers[0]
            transactions = [i for i in range(header_at + 1, end)
                            if i != footer and any(clean_text(value(rows[i], j)) for j in (policy, insured))]
            if not transactions:
                continue
            valid = all(i < footer and normalize_header(value(rows[i], cls)) == 'ICL'
                        and all(clean_text(value(rows[i], j)) for j in (policy, claim, insured))
                        and parse_number(value(rows[i], recovery)) is not None for i in transactions)
            control = parse_number(value(rows[footer], recovery))
            total = sum(parse_number(value(rows[i], recovery)) or 0 for i in transactions)
            if not valid or abs(abs(total) - abs(control)) > .02:
                exceptions.append(ExceptionRecord('WARN', 'hilal_claims_block_unresolved', path.name,
                    sheet, footer + 1, 'CREDIT LIFE closing label not applied: block identity/class codes '
                    'or recovery total could not be verified; source classes retained for review'))
                continue
            prepared[header_at].extend([None] * (width + 1 - len(prepared[header_at])))
            prepared[header_at][width] = 'HILAL CLAIMS BLOCK EVIDENCE'
            evidence = dict(rule='source_block_trailer', source_label='CREDIT LIFE', source_class='ICL',
                            footer_row=footer + 1, recovery_control=control)
            for i in transactions:
                prepared[i].extend([None] * (width + 1 - len(prepared[i])))
                # Workbook cells cannot supply dicts: only verified parser
                # evidence may activate this path, never a similarly named
                # user-provided column containing text/JSON.
                prepared[i][width] = dict(evidence)
                exceptions.append(ExceptionRecord('INFO', 'hilal_claims_block_class', path.name, sheet, i + 1,
                    f'Raw ICL belongs to CREDIT LIFE block closed at source row {footer + 1}; '
                    f'printed recovery {control} matches the absolute block recovery total. '
                    'Class resolves to Life; original ICL code and financial values retained.'))
        return prepared

    def check_claims_row(self, claim, *, row, cmap, **context):
        from datetime import date
        import re
        from src.domain.cre_cleaner.models import ExceptionRecord
        from src.domain.cre_cleaner.core.class_labels import normalize_class_label, is_unresolved_class

        col = cmap.get('class')
        raw = row[col] if col is not None and col < len(row) else None
        if isinstance(raw, date) or (isinstance(raw, str) and re.fullmatch(
                r'\s*\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)?\s*', raw)):
            claim.class_name = claim.audit.class_hint = ''
            claim.audit.class_source = 'invalid_column'
            claim.audit.class_resolution = {'rule': 'invalid_date_class', 'source_value': str(raw)}
            context['exceptions'].append(ExceptionRecord('WARN', 'hilal_invalid_class_value',
                context['path'].name, context['sheet'], context['excel_row'],
                f'CLASS contains date {raw!s}; treated as missing class, original value retained in audit'))
        parent_index = cmap.get('hilal_parent_evidence')
        if parent_index is not None and parent_index < len(row) and isinstance(row[parent_index], dict):
            evidence = row[parent_index]
            mapped = normalize_class_label(claim.class_name, self.class_map())
            marine = is_unresolved_class(claim.class_name)
            if mapped in {'', 'Other'} and (not marine or evidence['class_name'] in {'Marine Cargo', 'Marine Hull'}):
                claim.class_name = claim.audit.class_hint = evidence['class_name']
                claim.audit.class_source = 'banner'
                claim.audit.class_resolution = dict(evidence)
                context['exceptions'].append(ExceptionRecord('INFO', 'hilal_claims_parent_class',
                    context['path'].name, context['sheet'], context['excel_row'],
                    f"Source subclass {claim.audit.class_label_raw!r} uses explicit "
                    f"{evidence['source_label']} parent at row {evidence['parent_row']}; subclass retained in audit"))
        index = cmap.get('hilal_block_evidence')
        if index is None or index >= len(row) or not isinstance(row[index], dict):
            return
        evidence = row[index]
        if normalize_header(claim.audit.class_label_raw) == evidence['source_class']:
            claim.class_name = claim.audit.class_hint = 'Life'
            claim.audit.class_source = 'trailer'
            claim.audit.class_resolution = evidence

    def premium_header_indices(self, rows, found):
        headers = []
        for i, row in enumerate(rows):
            values = {v for v in row if isinstance(v, str)}
            if (
                values & {"POLICY NO", "POLICY NUMBER"}
                and values & {"INSURED NAME", "NAME OF INSURED"}
                and values & {"GROSS PREMIUM", "OUR SHARE GROSS PREMIUM", "PREMIUM"}
            ):
                headers.append(i)
        return headers or found

    def premium_header_row(self, rows, found):
        return self.premium_header_indices(rows, [found])[0]

    def premium_header_cells(self, raw_rows, header_at, header, group_row):
        h = [normalize_header(v) for v in header]
        groups = [normalize_header(v) for v in (group_row or [])]
        groups += [""] * max(0, len(h) - len(groups))
        if "NUMBER" in h:
            from src.domain.cre_cleaner.core.class_labels import normalize_class_label

            column = h.index("NUMBER")
            policy = next(
                (i for i, v in enumerate(h) if v in {"POLICY NO", "POLICY NUMBER"}),
                None,
            )
            samples = [
                r[column]
                for r in raw_rows[header_at + 1 : header_at + 11]
                if policy is not None
                and max(policy, column) < len(r)
                and r[policy]
                and r[column]
            ]
            cession_class = (column < len(groups) and groups[column] == 'CESSION'
                and policy is not None and 'NAME OF INSURED' in h
                and ('PREMIUM' in h or 'GROSS PREMIUM' in h))
            if cession_class or samples and all(
                normalize_class_label(str(v), self.class_map()) not in {"", "Other"}
                for v in samples
            ):
                h[column] = "CLASS"
        blocks = []
        for i in range(len(h) - 1):
            size = (
                3
                if h[i] == "PPN"
                and i + 2 < len(h)
                and h[i + 1] == "SUM INSURED"
                and h[i + 2] == "PREMIUM"
                else (
                    2
                    if h[i] == "SUM INSURED"
                    and h[i + 1] == "PREMIUM"
                    and (not i or h[i - 1] != "PPN")
                    else 0
                )
            )
            if size:
                blocks.append((i, size))
        labels = []
        if blocks:
            for i in range(blocks[0][0], len(h)):
                kind = self.premium_layout_rules.band_kind(groups[i])
                if kind and not (labels and labels[-1][2] == kind):
                    labels.append((i, groups[i], kind))
        if (
            blocks
            and len(blocks) == len(labels)
            and len({t[2] for t in labels}) == len(labels)
            and all(
                abs(start - position) <= 2
                for (start, _), (position, _, _) in zip(blocks, labels)
            )
        ):
            # Explicit group labels and complete SI/premium(/PPN) signatures
            # identify each band even when a title touches the previous band.
            for i in range(blocks[0][0], len(h)):
                if self.premium_layout_rules.band_kind(groups[i]):
                    groups[i] = ""
            for (start, _), (_, label, _) in zip(blocks, labels):
                groups[start] = label
            return super().premium_header_cells(raw_rows, header_at, h, groups)
        for i, size in blocks:
            labels = {
                g
                for g in groups[i : i + size]
                if self.premium_layout_rules.band_kind(g)
            }
            if size and len(labels) == 1:
                label = labels.pop()
                for j in range(i, i + size):
                    groups[j] = ""
                groups[i] = label
        return super().premium_header_cells(raw_rows, header_at, h, groups)

    def resolve_premium_class(self, *, sheet_hint, section, raw, **context):
        from src.domain.cre_cleaner.core.class_labels import normalize_class_label, is_unresolved_class
        if sheet_hint and not is_unresolved_class(sheet_hint):
            return sheet_hint, 'sheet', context['sheet']
        mapped = normalize_class_label(raw, self.class_map())
        parent = section or sheet_hint
        if mapped not in {'', 'Other'} and not is_unresolved_class(mapped):
            return mapped, 'column', raw
        if parent and not is_unresolved_class(parent):
            return parent, 'banner' if section else 'sheet', raw or parent
        return raw or parent, 'column' if raw else 'banner', raw or parent

    def batch_class_evidence(self, paths, *, context, include_fac=False):
        """Explicit rows only; never load persisted user aliases or infer recursively."""
        from src.domain.cre_cleaner.core.quarterly import parse_premium_file, parse_claims_file
        from src.domain.cre_cleaner.core.class_resolution import identify_rows, records, source_digest
        from src.domain.cre_cleaner.core.class_labels import row_class_label
        from src.domain.cre_cleaner.io.excel import workbook_read_cache
        from src.domain.cre_cleaner.models import PipelineResult

        result = PipelineResult()
        with workbook_read_cache():
            for path in sorted(set(paths)):
                # The pipeline ignores include_fac: facultative is never its
                # own treaty class and must not seed a missing treaty class.
                premium, _, _ = parse_premium_file(path, '', adapter=self, include_fac=False)
                paid, outstanding, _, _ = parse_claims_file(path, adapter=self, include_fac=False)
                digest = source_digest(path)
                for row in premium + paid + outstanding:
                    row.audit.source_digest = digest
                result.premium_rows.extend(premium)
                result.claims_rows.extend(paid)
                result.outstanding_rows.extend(outstanding)
        identify_rows(result, context)
        return [(r, kind, getter) for r, kind, getter in records(result)
                if row_class_label(r, getter(r), self.class_map()) != 'Facultative']

    def map_premium_columns(self, header, group_row, **kwargs):
        cm = super().map_premium_columns(header, group_row, **kwargs)
        # Some Life bands print SI and contribution with no share column.
        # Copy that pair without deriving or borrowing a percentage.
        for kind in ("ret", "sur", "fac"):
            si = [
                i
                for i, h in enumerate(header)
                if self.premium_layout_rules.band_kind(h) == kind
                and self.premium_layout_rules.looks_like_si(h)
            ]
            prem = [
                i
                for i, h in enumerate(header)
                if self.premium_layout_rules.band_kind(h) == kind
                and self.premium_layout_rules.looks_like_prem(h)
            ]
            if len(si) == len(prem) == 1:
                setattr(cm, kind + "_si", si[0])
                setattr(cm, kind + "_prem", prem[0])
        return cm

    def sheet_class_hint(self, sheet, detected):
        import re

        # This observed tab contains separate Fire, Marine and other sections.
        if re.sub(r"[^A-Z]+", " ", sheet.upper()).strip() == "FIRE GEN ACCT":
            return ""
        return detected

    def map_claims_columns(self, header, aliases, sheet_type=""):
        from src.domain.cre_cleaner.core.map_columns import is_share_header

        cm = super().map_claims_columns(header, aliases, sheet_type)
        norms = [normalize_header(h) for h in header]
        if 'HILAL CLAIMS BLOCK EVIDENCE' in norms:
            cm.mapping['hilal_block_evidence'] = norms.index('HILAL CLAIMS BLOCK EVIDENCE')
        if 'HILAL CLAIMS PARENT EVIDENCE' in norms:
            cm.mapping['hilal_parent_evidence'] = norms.index('HILAL CLAIMS PARENT EVIDENCE')
        for kind, explicit in [
            ("ret", "RETENTION PPN"),
            ("treaty", "RECOVERY PPN"),
            ("fac", "FAC PPN"),
        ]:
            field = "ppn_" + kind
            cm.mapping.pop(field, None)
            if explicit in norms:
                cm.mapping[field] = norms.index(explicit)
            else:
                money = cm.get("amount_" + kind)
                if (
                    money is not None
                    and money > 0
                    and is_share_header(norms[money - 1])
                ):
                    cm.mapping[field] = money - 1
        if sheet_type == "outstanding":
            for label in (
                "CURRENT RESERVE OUTSTANDING",
                "TOTAL OUTSTANDING",
                "OUTSTANDING AMOUNT",
                "RESERVE",
            ):
                if label in norms:
                    cm.mapping["total_claims"] = norms.index(label)
                    break
        return cm

    def retains_explicit_header_layout(self, header, cmap, rows, kind):
        import math
        import re
        from src.domain.cre_cleaner.core.normalize import parse_number, parse_date
        from src.domain.cre_cleaner.core.class_labels import normalize_class_label

        if kind != "premium" or not rows:
            return False
        insured, policy = cmap.get("insured"), cmap.get("policy_no")
        cls, start, end = (
            cmap.get("class"),
            cmap.get("period_from"),
            cmap.get("period_to"),
        )
        total, gross = cmap.get("sum_insured"), cmap.get("gross_premium")
        if any(i is None for i in (insured, policy, cls, start, end, total, gross)):
            return False
        if normalize_header(header[insured]) not in {
            "NAME",
            "ASSURED",
            "INSURED NAME",
            "NAME OF INSURED",
        }:
            return False

        def value(row, i):
            return row[i] if i is not None and i < len(row) else None

        for row in rows:
            if not re.fullmatch(
                r"[A-Z]{1,3}\d{5,12}", str(value(row, insured) or "").strip()
            ):
                return False
            if (
                "/" not in str(value(row, policy) or "")
                or normalize_class_label(value(row, cls), self.class_map()) != "Life"
            ):
                return False
            if any(parse_date(value(row, i)) is None for i in (start, end)):
                return False
            for whole, suffix in ((total, "si"), (gross, "prem")):
                amount = parse_number(value(row, whole))
                parts = [
                    parse_number(value(row, getattr(cmap, f"{band}_{suffix}")))
                    for band in ("ret", "sur", "fac")
                ]
                present = [v for v in parts if v is not None]
                if (
                    amount is None
                    or not present
                    or not math.isclose(
                        amount, sum(present), rel_tol=1e-6, abs_tol=0.02
                    )
                ):
                    return False
        return True

    def premium_source_layouts(
        self, rows, start, end, cmap, *, path, sheet, exceptions
    ):
        """Verify missing class cells in legacy Life money layouts.

        Identity changes have no trustworthy header and require source review.
        Money moves only when both source allocation ratios prove the offset.
        """
        from copy import deepcopy
        from datetime import date
        import math
        import re
        from src.domain.cre_cleaner.core.normalize import parse_date, parse_number
        from src.domain.cre_cleaner.models import ExceptionRecord

        maps, skip = {}, set()
        if normalize_header(sheet) != "LIFE":
            return maps, skip

        def value(row, i):
            return row[i] if i is not None and 0 <= i < len(row) else None

        def date_cell(v):
            return (
                isinstance(v, date)
                or isinstance(v, str)
                and bool(re.match(r"\d{4}-\d{2}-\d{2}", v))
            ) and parse_date(v) is not None

        for ridx in range(start, end):
            row = rows[ridx]
            pairs = [
                i
                for i in range(len(row) - 1)
                if date_cell(row[i]) and date_cell(row[i + 1])
            ]
            if len(pairs) != 1:
                continue
            dates = pairs[0]
            si, gp = cmap.get("sum_insured"), cmap.get("gross_premium")
            if si is None or gp is None:
                continue
            if (
                dates == cmap.get("period_from")
                and dates + 1 == cmap.get("period_to")
                and (
                    si == dates + 2
                    or parse_number(value(row, cmap.get("class"))) is None
                )
            ):
                continue
            offset = dates + 2 - si
            valid = (
                dates == cmap.get("period_from")
                and dates + 1 == cmap.get("period_to")
                and offset == -1
                and gp == si + 1
                and parse_number(value(row, cmap.get("class"))) is not None
                and bool(value(row, cmap.get("insured")))
                and parse_number(value(row, cmap.get("insured"))) is None
                and bool(value(row, cmap.get("policy_no")))
                and cmap.get("policy_no") < dates
            )
            local = deepcopy(cmap)
            if valid:
                local.mapping["sum_insured"] = si + offset
                local.mapping["gross_premium"] = gp + offset
                local.mapping.pop("class", None)
                totals = [
                    parse_number(value(row, si + offset)),
                    parse_number(value(row, gp + offset)),
                ]
                valid = all(v is not None and v != 0 for v in totals)
                bands = 0
                for band in ("ret", "sur", "fac"):
                    indices = [
                        getattr(cmap, band + "_" + s) for s in ("ppn", "si", "prem")
                    ]
                    if all(i is None for i in indices):
                        continue
                    if any(i is None for i in indices):
                        valid = False
                        break
                    numbers = [parse_number(value(row, i + offset)) for i in indices]
                    if any(v is None for v in numbers):
                        valid = False
                        break
                    ppn, amount, premium = numbers
                    if (
                        not valid
                        or not 0 <= ppn <= 100
                        or not all(
                            math.isclose(
                                part / whole, ppn / 100, rel_tol=1e-5, abs_tol=1e-7
                            )
                            for part, whole in zip((amount, premium), totals)
                        )
                    ):
                        valid = False
                        break
                    for suffix, index in zip(("ppn", "si", "prem"), indices):
                        setattr(local, band + "_" + suffix, index + offset)
                    bands += 1
                valid = valid and bands >= 2
            if valid:
                maps[ridx] = local
                reason, severity = "hilal_money_layout_verified", "INFO"
                detail = "Missing Life class cell: monetary columns aligned using unchanged identity/date columns and both SI/premium allocation ratios; source values preserved"
            else:
                skip.add(ridx)
                reason, severity = "hilal_source_layout_unresolved", "WARN"
                detail = "Life row changes column positions without a matching header; identity or monetary layout cannot be verified. Correct the source header before cleaning this row"
            exceptions.append(
                ExceptionRecord(severity, reason, path.name, sheet, ridx + 1, detail)
            )
        return maps, skip
