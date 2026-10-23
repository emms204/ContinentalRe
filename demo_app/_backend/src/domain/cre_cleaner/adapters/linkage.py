"""Linkage surplus/agricultural accounts and paid/outstanding loss tables."""

import re

from src.domain.cre_cleaner.adapters.source_layouts import (
    SourceLayoutAdapter,
    NamedAllocationRules,
)
from src.domain.cre_cleaner.core.class_labels import (
    BISOLA_CLASS_ORDER, approved_class_labels, banner_class_label, normalize_class_label,
)
from src.domain.cre_cleaner.core.normalize import clean_text, normalize_header
from src.domain.cre_cleaner.models import ExceptionRecord


_POLICY_CLASS_FILTER = re.compile(r"^POLICY\s+CLASS\s+FILTER\s*\(\s*(.*?)\s*\)\s*$", re.I)
_UW_YEAR_NOTE = re.compile(r"^(?:U\s*/?\s*W|UNDERWRITING)\s+(?:YEAR\s+)?(?:19|20)\d{2}$", re.I)
_MANUAL_PARENT_HEADINGS = frozenset({"LIABILITY", "GENERAL BUSINESS"})


class LinkagePremiumRules(NamedAllocationRules):
    def band_kind(self, label):
        if "OBLIG" in label:
            return "sur"
        return super().band_kind(label)


class LinkageJordansAdapter(SourceLayoutAdapter):
    cedant = "LINKAGE"
    broker = "JORDANS"
    class_label_extra = {
        label: "Agriculture"
        for label in (
            "LIVESTOCK INSURANCE",
            "MULTI PERILS CROP INSURANCE",
            "POULTRY INSURANCE",
            "FISH FARM INSURANCE",
            "FARM ALL RISKS",
            "AREA YIELD INDEX INSURANCE",
            "CROPS INSURANCE",
        )
    }
    premium_layout_rules = LinkagePremiumRules()
    premium_alias_extra = {"class": ["RISK TYPE"]}
    claims_alias_extra = {
        "insured": ["NAME OF INSURED CLAIMANT"],
        "total_claims": ["TOTAL CLAIMS PAID", "RESERVE"],
        "amount_ret": ["LINKAGE RETENTION", "RETAINED"],
        "details": ["DESCRIPTION OF LOSS", "DECRIPTION OF LOSS"],
    }
    claims_total_fallback = {"paid": ("AMOUNT PAID",), "outstanding": ("RESERVE",)}
    premium_section_class_first = True

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        """Index report-class boundaries without moving or rewriting source cells.

        Older reports put Policy Class Filter and Branch Filter on the same
        row; newer reports use lone class banners. Empty/unknown filters also
        form boundaries, so the previous section cannot leak into the next.
        """
        sections = []
        allowed = approved_class_labels(self.class_map())
        for n, row in enumerate(rows, 1):
            filled = [clean_text(v) for v in row if clean_text(v)]
            matches = [_POLICY_CLASS_FILTER.fullmatch(v) for v in filled]
            matches = [m for m in matches if m]
            # Report controls have no transaction cells. Do not interpret an
            # insured name or risk description containing this phrase as a filter.
            if matches and len(filled) <= 3 and all(
                    _POLICY_CLASS_FILTER.fullmatch(v) or "FILTER" in normalize_header(v)
                    for v in filled):
                labels = {m.group(1).strip() for m in matches}
                raw = next(iter(labels)) if len(labels) == 1 else "CONFLICTING SOURCE FILTERS"
                if raw.upper() in {"", "NONE", "ALL", "ALL CLASSES", "N/A"}:
                    raw = ""
                sections.append((n, raw))
                exceptions.append(ExceptionRecord(
                    "INFO", "linkage_source_class_section", path.name, sheet, n,
                    f"Source Policy Class Filter={raw!r}; applies until the next class boundary",
                ))
            elif filled and len(filled) <= 3 and all(_UW_YEAR_NOTE.fullmatch(v) for v in filled[1:]):
                label = banner_class_label(filled[0])
                # These unsupported parent headings still reset the section;
                # a user must map them, rather than inheriting the prior class.
                if label in allowed or label == "MARINE" or normalize_header(filled[0]) in _MANUAL_PARENT_HEADINGS:
                    sections.append((n, filled[0]))
        # Adapter instances belong to one run. File+sheet scoping also permits
        # safe reuse across a multi-file run; every read replaces its own index.
        if not hasattr(self, "_source_class_sections"):
            self._source_class_sections = {}
        self._source_class_sections[(str(path.resolve()), sheet)] = sections
        return [list(row) for row in rows]

    def resolve_premium_class(self, *, sheet_hint, section, raw, exceptions, path, sheet, excel_row):
        sections = getattr(self, "_source_class_sections", {}).get((str(path.resolve()), sheet), ())
        prior = [event for event in sections if event[0] < excel_row]
        parent = prior[-1][1] if prior else (section or sheet_hint)
        source = "banner" if prior or section else "sheet"
        allowed = approved_class_labels(self.class_map())
        mapped_parent = normalize_class_label(parent, self.class_map())
        mapped_raw = normalize_class_label(raw, self.class_map())
        if parent.upper() == "MARINE":
            if mapped_raw in {"Marine Cargo", "Marine Hull"}:
                return mapped_raw, "column", raw
            return "MARINE", source, raw or parent
        if mapped_parent in allowed:
            explicit = normalize_header(raw) in {normalize_header(v) for v in BISOLA_CLASS_ORDER}
            if explicit and mapped_raw != mapped_parent:
                exceptions.append(ExceptionRecord(
                    "WARN", "linkage_class_conflict", path.name, sheet, excel_row,
                    f"Source section {parent!r} conflicts with explicit row class {raw!r}; review required",
                ))
                return "CLASS CONFLICT", "conflict", raw
            return mapped_parent, source, raw or parent
        if parent:
            # An unconfirmed source parent requires a user decision; unknown
            # nonempty text is not a resolved class or a reason to reuse Fire.
            return parent, source, raw or parent
        return (mapped_raw if mapped_raw in allowed else raw), "column", raw

    def map_premium_columns(self, header, group_row, **kwargs):
        cm = super().map_premium_columns(header, group_row, **kwargs)
        cm.combine_treaty_layers = True
        return cm



class LinkageAgricAdapter(LinkageJordansAdapter):
    broker = "AGRIC"
    settings = {"single_class": "Agriculture"}

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        """Name the old three-column allocation bands only after validation.

        Some AGRIC exports label the first cell of each band but omit all
        child headings. Source share/SI/premium arithmetic establishes their
        roles; no financial value is derived, rescaled or filled.
        """
        from math import isclose, isfinite
        from src.domain.cre_cleaner.core.normalize import parse_number

        result = super().prepare_source_sheet(sheet, rows, path=path, exceptions=exceptions)
        required = {'POLICY NO', 'INSURED', 'SUM INSURED', 'GROSS PREMIUM'}
        for hi, header in enumerate(rows):
            norms = [normalize_header(v) for v in header]
            if not required <= set(norms) or not {'NET RETENTION', 'TREATY ACCOUNT SURPLUS'} <= set(norms):
                continue
            bands = [norms.index('NET RETENTION'), norms.index('TREATY ACCOUNT SURPLUS')]
            if (abs(bands[0]-bands[1]) < 3
                    or any(i+2 >= len(norms) or any(norms[i+1:i+3]) for i in bands)):
                continue  # Existing child labels belong to the ordinary mapper.
            pi, ni = norms.index('POLICY NO'), norms.index('INSURED')
            si, gp = norms.index('SUM INSURED'), norms.index('GROSS PREMIUM')
            end = next((i for i in range(hi+1,len(rows))
                        if required <= {normalize_header(v) for v in rows[i]}),len(rows))
            transactions = [r for r in rows[hi+1:end] if max(pi,ni) < len(r)
                            and clean_text(r[pi]) and clean_text(r[ni])
                            and normalize_header(r[pi]) not in {'TOTAL','GRAND TOTAL'}]
            valid = bool(transactions)
            proved = [False,False]
            for r in transactions:
                def number(col):
                    nonlocal valid
                    raw = r[col] if col < len(r) else None
                    value = parse_number(raw)
                    if clean_text(raw) and (value is None or not isfinite(value)):
                        valid = False
                    return value
                bases = [number(si),number(gp)]
                for bi,start in enumerate(bands):
                    share, insured, premium = [number(start+j) for j in range(3)]
                    if share is None:
                        continue  # A blank source field is never filled.
                    if abs(share)>100:
                        valid = False
                        continue
                    comparisons = [(base,amount) for base,amount in zip(bases,[insured,premium])
                                   if base is not None and amount is not None]
                    # Some source rates are printed to four decimal places
                    # (e.g. .2667) while the source SI is a round retained cap.
                    # Half a displayed rate unit validates that column role;
                    # it never changes the supplied rate or monetary value.
                    def matches(factor):
                        rounded = (isclose(factor,round(factor,4),abs_tol=1e-12,rel_tol=0)
                                   and not isclose(factor,round(factor,3),abs_tol=1e-12,rel_tol=0))
                        return all(isclose(amount,base*factor,rel_tol=1e-8,
                                           abs_tol=.03 + (abs(base)*.00005 if rounded else 0))
                                   for base,amount in comparisons)
                    fits = any(matches(factor) for factor in (share,share/100))
                    valid = valid and fits
                    proved[bi] |= fits and any(base != 0 and amount != 0 for base,amount in comparisons)
            if not valid or not all(proved):
                exceptions.append(ExceptionRecord('WARN','source_layout_ambiguous',path.name,sheet,hi+1,
                    'Unlabelled NET RETENTION / TREATY ACCOUNT-SURPLUS bands could not be verified '
                    'as proportion, sum insured and premium from the source row arithmetic. '
                    'No allocation fields inferred; review the source layout.'))
                continue
            for start,label in zip(bands,['RETENTION','TREATY ACCOUNT SURPLUS']):
                result[hi][start:start+3] = [label+' PPN',label+' SUM INSURED',label+' PREMIUM']
            exceptions.append(ExceptionRecord('INFO','linkage_allocation_header_restored',path.name,sheet,hi+1,
                'Source NET RETENTION and TREATY ACCOUNT-SURPLUS bands verified as proportion / '
                'sum insured / premium from transaction arithmetic; child labels supplied only '
                'in parser memory. Original cell values, signs, blanks and coordinates preserved.'))
        return result

    def check_premium_row(self, prow, *, exceptions, path, sheet, **context):
        super().check_premium_row(prow, exceptions=exceptions, path=path, sheet=sheet, **context)
        prior = [e for e in getattr(self, '_source_class_sections', {}).get((str(path.resolve()),sheet),())
                 if e[0] < prow.audit.source_row]
        if not prior:
            return
        parent = prior[-1][1]
        raw = prow.audit.class_label_raw
        pc, rc = normalize_class_label(parent,self.class_map()),normalize_class_label(raw,self.class_map())
        allowed = approved_class_labels(self.class_map())
        if pc not in allowed or rc not in allowed or pc == rc:
            return
        prow.audit.class_hint = 'CLASS CONFLICT'
        prow.audit.class_source = 'conflict'
        prow.audit.class_resolution = dict(rule='conflicting_source_classes',section_class=pc,
            section_label=parent,subclass_label=raw,row_class=raw,product_class=rc,
            candidates=sorted({pc,rc}),expected_class='Agriculture')
        exceptions.append(ExceptionRecord('WARN','linkage_source_class_conflict',path.name,sheet,
            prow.audit.source_row,f'Source Policy Class Filter {parent!r} ({pc}) disagrees with '
            f'row Risk Type {raw!r} ({rc}). Confirm this source record; no reusable alias resolves the conflict.'))

    def class_map(self):
        cmap = super().class_map()
        # Run wrappers retain this adapter-local base; no global map changes.
        cmap.source_parent_first = True
        return cmap

    def check_claims_row(self, claim, *, row, cmap, exceptions, path, sheet, excel_row):
        # Validate source amounts without replacing them or treating a blank
        # mapped allocation as zero. An absent FAC band contributes nothing.
        parts = [claim.amount_ret, claim.amount_treaty]
        if cmap.get("amount_fac") is not None:
            parts.append(claim.amount_fac)
        if claim.total_claims is None or any(v is None for v in parts):
            return
        difference = sum(parts) - claim.total_claims
        if abs(difference) > 1.0:
            exceptions.append(ExceptionRecord(
                "WARN", "linkage_source_allocation_mismatch", path.name, sheet, excel_row,
                f"Source allocations total {sum(parts):,.2f}; source claim total "
                f"{claim.total_claims:,.2f}; difference {difference:,.2f}. "
                "Source values preserved; check the original allocations.",
            ))
