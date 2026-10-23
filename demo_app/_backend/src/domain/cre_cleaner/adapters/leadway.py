"""Leadway treaty returns: explicit source money; no residual calculations."""

from src.domain.cre_cleaner.adapters.source_layouts import SourceLayoutAdapter


class LeadwayUaibAdapter(SourceLayoutAdapter):
    cedant = "LEADWAY"
    broker = "UAIB"
    class_label_extra = {"CARGO": "Marine Cargo", "MH": "Marine Hull", "BONDS": "Bond"}
    premium_alias_extra = {
        "period_from": ["WEF"],
        "period_to": ["WET"],
        "uw_year": ["U/W YEAR", "UW/YR", "UW YEAR"],
        "class": ["CLASS", "ACCOUNT GROUP", "SUB CLASS"],
    }
    claims_alias_extra = {
        "insured": ["INSURED FULL NAME"],
        "period_from": ["COVER FROM DATE", "POLICY WEF DATE", "WEF"],
        "period_to": ["COVER TO DATE", "POLICY WET DATE", "WET"],
        "total_claims": [
            "TOTAL LOSS PAID",
            "LOSS PAID",
            "OUTSTANDING RESERVE",
            "OUTSTANDING AMT",
        ],
        "amount_ret": ["L/WAY SHARE", "L WAY RETN", "RETENTION", "NET PROP", "LAC RETAINED AMOUNT"],
        "amount_treaty": ["QUOTA SHARE TREATY", "QUOTA SHARE", "TREATY"],
        "amount_fac": ["FACULATAIVE", "FAC BALANCE", "FAC PROP", "FACULTATIVE", "FAC"],
        "date_of_loss": ["ACCIDENT DATE"],
        "details": ["LOSS TYPE", "LOSS DESCRIPTION"],
        "class": ["CLASS OF BUSINESS", "CLASS"],
    }

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        from src.domain.cre_cleaner.core.class_labels import (
            normalize_class_label,
            approved_class_labels,
            banner_class_label,
        )
        from src.domain.cre_cleaner.core.normalize import normalize_header, clean_text
        from src.domain.cre_cleaner.models import ExceptionRecord

        result = [list(r) for r in rows]
        # Legacy UAIB titles combine the explicit class, report date and
        # currency in one long cell. The generic banner length guard correctly
        # rejects long prose; isolate only a proven CLASS-labelled report title
        # here, leaving all physical source rows and financial cells intact.
        import re
        for ri, row in enumerate(rows):
            filled = [(ci, value) for ci, value in enumerate(row) if clean_text(value)]
            if len(filled) != 1:
                continue
            ci, value = filled[0]
            match = re.fullmatch(
                r'\s*CLASS(?:\s+OF\s+BUSINESS)?\s*:\s*(.+?)\s+'
                r'(?:OUTSTANDING|PAID)\s+CLAIMS?\s+REPORT\b.*',
                str(value), flags=re.IGNORECASE | re.DOTALL)
            if not match:
                continue
            label = match.group(1).strip()
            canonical = banner_class_label(label)
            if not canonical or canonical not in approved_class_labels(self.class_map()):
                continue
            result[ri][ci] = 'CLASS: ' + label
            exceptions.append(ExceptionRecord(
                'INFO', 'class_from_report_title', path.name, sheet, ri + 1,
                f'Explicit class {label!r} separated from report metadata for parsing; '
                f'original title: {value}'))
        target = max((len(r) for r in rows), default=0)
        group = sub = None
        allowed = approved_class_labels(self.class_map())
        for i, row in enumerate(rows):
            norms = [normalize_header(v) for v in row]
            if (
                "ACCOUNT GROUP" in norms
                and "SUB CLASS" in norms
                and any("POLICY" in v for v in norms)
            ):
                group, sub = norms.index("ACCOUNT GROUP"), norms.index("SUB CLASS")
                result[i] += [""] * (target - len(result[i]))
                result[i].append("CLASS")
                exceptions.append(
                    ExceptionRecord(
                        "INFO",
                        "class_from_source_columns",
                        path.name,
                        sheet,
                        i + 1,
                        "Approved subclass refines a broad account group; otherwise account group is retained; original columns preserved",
                    )
                )
            elif group is not None and max(group, sub) < len(row):
                g, s = clean_text(row[group]), clean_text(row[sub])
                gc, sc = normalize_class_label(
                    g, self.class_map()
                ), normalize_class_label(s, self.class_map())
                chosen = s if sc in allowed and (gc not in allowed or gc == sc) else g
                result[i] += [""] * (target - len(result[i]))
                result[i].append(chosen)
        return result

    claims_total_fallback = {
        "paid": ("LOSS PAID", "AMOUNT PAID"),
        "outstanding": ("OUTSTANDING RESERVE", "OUTSTANDING AMOUNT"),
    }

    def reconcile_source_overlaps(self, result, source_paths):
        """A named claims revision controls only a complete matching population.

        Money is deliberately excluded from identity: revisions correct amounts.
        Multiplicity, currencies and all nonfinancial transaction fields must
        agree. This never deduplicates repeated payments within a source.
        """
        from collections import Counter, defaultdict
        from pathlib import Path
        import re
        from src.domain.cre_cleaner.models import ExceptionRecord

        families = defaultdict(dict)
        for path in set(map(Path, source_paths)):
            stem = path.stem.upper()
            base = re.sub(r'\b(UPDATED|REVISED|AMENDED|CORRECTED)\b', '', stem)
            base = ' '.join(base.split())
            families[base][path.name] = base != ' '.join(stem.split())

        def identity(row):
            fields = ('claim_no', 'policy_no', 'insured', 'class_name', 'date_of_loss',
                      'paid_date', 'uw_yr', 'period_from', 'period_to', 'details')
            return tuple(str(getattr(row, field) or '').strip().upper() for field in fields) + (
                str(row.audit.currency or '').upper(),)

        exclusions = []
        for attr, kind in [('claims_rows', 'paid'), ('outstanding_rows', 'outstanding')]:
            populations = defaultdict(list)
            for row in getattr(result, attr):
                populations[row.audit.source_filename].append(row)
            for members in families.values():
                originals = [n for n, revised in members.items() if not revised and populations[n]]
                revisions = [n for n, revised in members.items() if revised and populations[n]]
                if not originals or not revisions:
                    continue  # Single source / kind only in one version remains eligible.
                names = originals + revisions
                valid = len(originals) == len(revisions) == 1
                signatures = [Counter(identity(row) for row in populations[name]) for name in names]
                valid = valid and all(s == signatures[0] for s in signatures[1:])
                valid = valid and all(row.claim_no and row.policy_no for name in names for row in populations[name])
                if not valid:
                    result.exceptions.append(ExceptionRecord(
                        'ERROR', 'source_overlap_ambiguous', names[0],
                        detail=f'Leadway {kind} source versions {names} do not have one complete matching '
                               'transaction population (including repeated payments and currency). '
                               'Confirm the controlling source; no revision rows removed.'))
                    continue
                exclusions.append((attr, kind, originals[0], revisions[0]))
        if any(e.reason == 'source_overlap_ambiguous' for e in result.exceptions):
            return
        for attr, kind, older, revised in exclusions:
            rows = getattr(result, attr)
            count = sum(r.audit.source_filename == older for r in rows)
            setattr(result, attr, [r for r in rows if r.audit.source_filename != older])
            detail = (f'{count} {kind} rows superseded by {revised}: complete source transaction '
                      'identities, currencies and multiplicities match; revised financial values retained.')
            result.exceptions.append(ExceptionRecord('INFO', 'leadway_revision_not_loaded', older, detail=detail))
            for audit in result.source_audit:
                if audit.source_filename == older and audit.sheet_type == kind:
                    audit.sheet_type = f'secondary:{kind}'
                    audit.rows_skipped += audit.rows_kept
                    audit.rows_kept = 0
                    audit.notes += '; ' + detail
                    audit.parsed_totals, audit.parsed_rows = {}, {}
                    audit.footer_totals, audit.footer_cells = {}, {}


class LeadwayScibAdapter(LeadwayUaibAdapter):
    broker = "SCIB"


class LeadwayArkAdapter(LeadwayUaibAdapter):
    broker = "ARK"
    settings = {"single_class": "Bond"}


class LeadwayAgricAdapter(LeadwayUaibAdapter):
    broker = "AGRIC"
    settings = {"single_class": "Agriculture"}
    class_label_extra = {
        **LeadwayUaibAdapter.class_label_extra,
        "FARM PROPERTY & PRODUCE": "Agriculture",
    }
    class_label_exact_only = frozenset({"FARM PROPERTY & PRODUCE"})

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        from src.domain.cre_cleaner.core.class_labels import normalize_class_label
        from src.domain.cre_cleaner.core.normalize import normalize_header, clean_text
        from src.domain.cre_cleaner.models import ExceptionRecord

        result = super().prepare_source_sheet(sheet, rows, path=path, exceptions=exceptions)
        target = max((len(row) for row in rows), default=0)
        group = sub = None
        refined = []
        for i, row in enumerate(rows):
            norms = [normalize_header(v) for v in row]
            if "ACCOUNT GROUP" in norms and "SUB CLASS" in norms and any("POLICY" in v for v in norms):
                group, sub = norms.index("ACCOUNT GROUP"), norms.index("SUB CLASS")
            elif group is not None and max(group, sub) < len(row) and len(result[i]) > target:
                # MISCELLANEOUS is a broad system account group in these
                # agricultural returns. A named agricultural product refines
                # it; an explicit Fire/Bond/General Accident parent does not.
                if (norms[group] in {"MISC", "MISCELLANEOUS", "MISCELLANEOUS ACCIDENT"}
                        and normalize_class_label(row[sub], self.class_map()) == "Agriculture"):
                    result[i][target] = clean_text(row[sub])
                    refined.append(i + 1)
        # Later claims exports use a generic reporting Class Of Business
        # (including FIRE) beside the actual agricultural product. This is a
        # different source schema from an explicit premium parent conflict.
        parent = sub = None
        for i, row in enumerate(rows):
            norms = [normalize_header(v) for v in row]
            if ("CLASS OF BUSINESS" in norms and "SUBCLASS OF BUSINESS" in norms
                    and any("POLICY" in v for v in norms)):
                parent, sub = norms.index("CLASS OF BUSINESS"), norms.index("SUBCLASS OF BUSINESS")
                result[i] += [""] * (target - len(result[i]))
                result[i].append("CLASS")
            elif parent is not None and max(parent, sub) < len(row):
                p, s = clean_text(row[parent]), clean_text(row[sub])
                agricultural = normalize_class_label(s, self.class_map()) == "Agriculture"
                result[i] += [""] * (target - len(result[i]))
                result[i].append(s if agricultural else p)
                if agricultural and normalize_class_label(p, self.class_map()) != "Agriculture":
                    exceptions.append(ExceptionRecord(
                        "INFO", "class_from_agric_subclass", path.name, sheet, i + 1,
                        f"Source reporting CLASS OF BUSINESS={p!r}; explicit agricultural product "
                        f"SUBCLASS OF BUSINESS={s!r} supplies the treaty class; both source cells retained."))
        if refined:
            exceptions.append(ExceptionRecord(
                "INFO", "class_from_agric_subclass", path.name, sheet, 0,
                f"{len(refined)} agricultural subclass(es) refine the broad miscellaneous account group; "
                "original ACCOUNT GROUP and SUB CLASS cells retained, subclass used as class evidence."))
        return result

    def map_claims_columns(self, header, aliases, sheet_type=""):
        from src.domain.cre_cleaner.core.normalize import normalize_header

        cm = super().map_claims_columns(header, aliases, sheet_type)
        norms = [normalize_header(v) for v in header]
        if {"CLASS OF BUSINESS", "SUBCLASS OF BUSINESS", "CLASS"} <= set(norms):
            cm.mapping["class"] = norms.index("CLASS")
        return cm


class LeadwayYoaAdapter(LeadwayUaibAdapter):
    broker = "YOA"
    settings = {"single_class": "Terrorism & PVT"}

    def class_map(self):
        cmap = super().class_map()
        # Only source-record decisions may cross YOA's treaty default. The
        # default, stored aliases and other single-class adapters stay strict.
        cmap.allow_record_class_override = True
        return cmap

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        from src.domain.cre_cleaner.core.normalize import normalize_header, clean_text
        result = super().prepare_source_sheet(sheet, rows, path=path, exceptions=exceptions)
        evidence = getattr(self, '_yoa_source_classes', {})
        parent = sub = None
        for i, row in enumerate(rows):
            norms = [normalize_header(v) for v in row]
            if 'ACCOUNT GROUP' in norms and 'SUB CLASS' in norms and any('POLICY' in n for n in norms):
                parent, sub = norms.index('ACCOUNT GROUP'), norms.index('SUB CLASS')
            elif 'CLASS OF BUSINESS' in norms and 'SUBCLASS OF BUSINESS' in norms:
                parent, sub = norms.index('CLASS OF BUSINESS'), norms.index('SUBCLASS OF BUSINESS')
            elif parent is not None and max(parent,sub) < len(row):
                evidence[(str(path.resolve()), sheet, i+1)] = (clean_text(row[parent]),clean_text(row[sub]))
        self._yoa_source_classes = evidence
        return result

    def _check_source_classes(self, row, *, exceptions, path, sheet):
        from src.domain.cre_cleaner.core.class_labels import normalize_class_label, approved_class_labels
        from src.domain.cre_cleaner.models import ExceptionRecord
        labels = getattr(self, '_yoa_source_classes', {}).get((str(path.resolve()), sheet, row.audit.source_row))
        if not labels:
            return
        parent, sub = labels
        pc, sc = [normalize_class_label(v, self.class_map()) for v in labels]
        allowed = approved_class_labels(self.class_map())
        if pc not in allowed or sc not in allowed or pc == sc:
            return
        row.audit.class_source = 'conflict'
        row.audit.class_hint = 'CLASS CONFLICT'
        row.audit.class_label_raw = sub
        if hasattr(row, 'class_name'):
            row.class_name = 'CLASS CONFLICT'
        row.audit.class_resolution = dict(
            rule='conflicting_source_classes', section_class=pc, section_label=parent,
            section_field='Account group / source parent', row_class=sub, subclass_label=sub,
            product_class=sc, expected_class=self.setting('single_class'),
            candidates=sorted({pc, sc}))
        exceptions.append(ExceptionRecord('WARN', 'yoa_source_class_conflict', path.name, sheet,
            row.audit.source_row, f'Source parent {parent!r} ({pc}) and subclass {sub!r} ({sc}) disagree. '
            'YOA defaults to Terrorism & PVT; choose a class for these source records. No alias resolves this conflict.'))

    def check_premium_row(self, prow, *, exceptions, path, sheet, **context):
        super().check_premium_row(prow, exceptions=exceptions, path=path, sheet=sheet, **context)
        self._check_source_classes(prow, exceptions=exceptions, path=path, sheet=sheet)

    def check_claims_row(self, crow, *, exceptions, path, sheet, **context):
        self._check_source_classes(crow, exceptions=exceptions, path=path, sheet=sheet)
