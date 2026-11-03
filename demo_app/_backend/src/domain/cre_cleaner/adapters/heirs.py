"""HEIRS adapters (Direct, HIB, Jomola) — HEIRS rules only (IMPL-20260929-04).

* 'RTN%' is the HEIRS retention share column (band = retention).
* HEIRS/JOMOLA submissions include production / listing dumps (tabs named
  PROD / PRODUCTION / LISTING) that are not premium bordereaux.
"""
from __future__ import annotations

import json
import re

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.core.class_labels import (
    BISOLA_CLASS_ORDER, banner_class_label, normalize_class_label,
)
from src.domain.cre_cleaner.core.detect import SheetTypeRules
from src.domain.cre_cleaner.core.map_columns import PremiumLayoutRules
from src.domain.cre_cleaner.core.normalize import clean_text, normalize_header, parse_number
from src.domain.cre_cleaner.models import ExceptionRecord


class HeirsPremiumRules(PremiumLayoutRules):
    PPN_EXACT = PremiumLayoutRules.PPN_EXACT | {"RTN%"}
    PPN_PCT_BAND_WORDS = ("RTN",) + PremiumLayoutRules.PPN_PCT_BAND_WORDS
    RET_BAND_WORDS = PremiumLayoutRules.RET_BAND_WORDS + ("RTN%",)


HEIRS_PREMIUM_RULES = HeirsPremiumRules()


class HeirsJomolaPremiumRules(HeirsPremiumRules):
    def block_for_ppn(self, pi, norms, si_idxs, prem_idxs):
        # JOMOLA's SI/Premium/% triples end with their share. The next
        # triple must not donate its amounts to the preceding percentage.
        own_band = self.band_kind(norms[pi])
        if (pi >= 2 and self.looks_like_si(norms[pi - 2])
                and self.looks_like_prem(norms[pi - 1])
                and (not own_band or own_band in {self.band_kind(norms[pi - 2]), self.band_kind(norms[pi - 1])})):
            return pi, pi - 2, pi - 1
        return super().block_for_ppn(pi, norms, si_idxs, prem_idxs)


HEIRS_JOMOLA_PREMIUM_RULES = HeirsJomolaPremiumRules()
# 'Q1,2025 PAID CLAIMS T-RECOVERY': T- is treaty, not part of a class name.
HEIRS_CLASS_NOISE = frozenset({"T-"})
HEIRS_SHEET_RULES = SheetTypeRules(class_noise_words=HEIRS_CLASS_NOISE)
HEIRS_JOMOLA_SHEET_RULES = SheetTypeRules(
    dump_tokens=frozenset({"PROD", "PRODUCTION", "LISTING"}),
    class_noise_words=HEIRS_CLASS_NOISE,
)


class _HeirsAdapter(QuarterlyWorkbookAdapter):
    premium_layout_rules = HEIRS_PREMIUM_RULES
    sheet_type_rules = HEIRS_SHEET_RULES


class _HeirsEvidenceAdapter(_HeirsAdapter):
    """Shared source-evidence rules for HEIRS treaty submissions."""

    strict_sheet_class_hints = True
    premium_section_class_first = True

    def premium_header_cells(self, raw_rows, header_at, header, group_row):
        header = list(header)
        labels = [normalize_header(v) for v in header]
        if not {"START DATE", "END DATE", "PERIOD", "INSURED", "POLICY NUMBER"}.issubset(labels):
            return header, group_row
        period, insured, policy = (labels.index(v) for v in ("PERIOD", "INSURED", "POLICY NUMBER"))
        durations = []
        transaction_seen = False
        for row in raw_rows[header_at + 1:]:
            if len(row) <= max(period, insured, policy):
                continue
            if not clean_text(row[insured]) or not clean_text(row[policy]):
                continue
            if normalize_header(row[policy]) == "POLICY NUMBER":
                continue
            transaction_seen = True
            if clean_text(row[period]):
                durations.append(parse_number(row[period]))
        # Rename only a proven day-count column in the parser's header copy.
        # Genuine date ranges and other workbook layouts keep their semantics.
        if transaction_seen and all(v is not None and abs(v) <= 3660 for v in durations):
            header[period] = "COVER DURATION DAYS"
        return header, group_row

    def map_premium_columns(self, header, group_row, *, path=None, sheet="", exceptions=None):
        cm = super().map_premium_columns(header, group_row, path=path, sheet=sheet, exceptions=exceptions)
        if "COVER DURATION DAYS" in header and exceptions is not None:
            exceptions.append(ExceptionRecord(
                "INFO", "heirs_period_is_duration", path.name if path else "", sheet, 0,
                "Numeric PERIOD contains day counts; explicit START DATE and END DATE supply cover dates",
            ))
        return cm

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        """Expose section banners whose other cells contain only limit labels.

        Work on a copy; physical row numbers and all transaction cells survive.
        The premium signature prevents applying this repair to claims layouts.
        """
        result = [list(row) for row in rows]
        if not any({"RISKS TYPE", "POLICY NUMBER", "PREMIUM"}.issubset(
                {normalize_header(v) for v in row}) for row in rows):
            return result
        for i, row in enumerate(rows):
            filled = [(j, clean_text(v)) for j, v in enumerate(row) if clean_text(v)]
            if len(filled) < 2 or not banner_class_label(filled[0][1]):
                continue
            if all(normalize_header(v) in {"LIMIT", "SUM INSURED"} for _, v in filled[1:]):
                for j, _ in filled[1:]:
                    result[i][j] = None
                exceptions.append(ExceptionRecord(
                    "INFO", "heirs_section_banner_normalized", path.name, sheet, i + 1,
                    "Source class section retained; ancillary Limit/Sum Insured labels hidden in parser copy",
                ))
        return result

    def _resolve_class(self, parent, raw, source, *, exceptions, path, sheet, excel_row):
        mapped_parent = normalize_class_label(parent, self.class_map())
        mapped_raw = normalize_class_label(raw, self.class_map())
        # Only an explicit top-level class contradicts the parent. A risk
        # description is detail, even when a generic subclass alias exists.
        explicit = normalize_header(raw) in {normalize_header(v) for v in BISOLA_CLASS_ORDER}
        if mapped_parent not in {"", "Other"}:
            if explicit and mapped_raw != mapped_parent:
                exceptions.append(ExceptionRecord(
                    "WARN", "heirs_class_conflict", path.name, sheet, excel_row,
                    f"Source parent {parent!r} conflicts with explicit row class {raw!r}; review required",
                ))
                # Nonblank, noncanonical evidence must survive policy inference
                # and remain blocked by the final class approval guard.
                return "CLASS CONFLICT", "conflict", raw
            return mapped_parent, source, raw or parent
        if mapped_raw not in {"", "Other"}:
            return mapped_raw, "column", raw
        return raw or parent, "column" if raw else source, raw or parent

    def resolve_premium_class(self, *, sheet_hint, section, raw, **context):
        return self._resolve_class(section or sheet_hint, raw,
                                   "banner" if section else "sheet", **context)

    def map_claims_columns(self, header, aliases, sheet_type=""):
        cm = super().map_claims_columns(header, aliases, sheet_type)
        labels = [normalize_header(v) for v in header]
        # Class evidence is independent of the money layout. In particular,
        # 2023/2024 paid schedules do not have the 2022 RESERVE/TOTAL signature.
        parents = ("CLASS", "POLICY CLASS", "POL CLASS")
        subclasses = ("SUB CLASS", "SUBCLASS")
        for field, choices in (("class", parents), ("subclass", subclasses)):
            index = next((labels.index(v) for v in choices if v in labels), None)
            if index is not None:
                cm.mapping[field] = index
            elif field == "class" and any(v in labels for v in subclasses):
                cm.mapping.pop("class", None)
        # Q2/Q4 paid layout: bind by exact labels, never fuzzy INSURED ->
        # SUM INSURED or TOTAL CLAIMS -> RESERVE. Column1 is trusted only in
        # this complete source signature.
        signature = {"CLASS", "SUB CLASS", "RESERVE", "AMOUNT PAID", "TOTAL", "SUM INSURED"}
        has_identity = (bool({"INSURED", "COLUMN1"}.intersection(labels))
                        and bool({"ESKA CLAIM NO", "CLAIMNO", "CLAIM NO"}.intersection(labels))
                        and bool({"POLICYNO", "POLICY NO", "POLICY NUMBER"}.intersection(labels)))
        if sheet_type == "paid" and has_identity and signature.issubset(labels):
            bindings = {
                "insured": ("INSURED", "COLUMN1"),
                "claim_no": ("ESKA CLAIM NO", "CLAIMNO", "CLAIM NO"),
                "class": ("CLASS",), "subclass": ("SUB CLASS",),
                "total_claims": ("TOTAL CLAIMS PAID", "AMOUNT PAID"),
                "ppn_treaty": ("TREATY %",), "amount_treaty": ("TREATY",),
                "ppn_fac": ("FAC%", "FAC %"), "amount_fac": ("FAC",),
                "details": ("DECRIPTION OF LOSS", "DESCRIPTION OF LOSS"),
                "sum_insured": ("SUM INSURED",),
                "source_reserve": ("RESERVE",), "source_paid": ("AMOUNT PAID",),
                "source_fees": ("ADJUSTERS FEE",), "source_total": ("TOTAL",),
            }
            for field, choices in bindings.items():
                cm.mapping.pop(field, None)
                for label in choices:
                    if label in labels:
                        cm.mapping[field] = labels.index(label)
                        break
            cm.tie_log.append("HEIRS paid signature: exact parent class, insured, paid and allocation columns")

        if sheet_type == "paid":
            # Select by the header, never by whether a row's paid cell is
            # populated. An explicit paid total includes only what the source
            # says it includes; we do not calculate fees or fall back to reserve.
            paid_headers = ("TOTAL CLAIMS PAID", "TOTAL AMOUNT PAID", "AMOUNT PAID", "CLAIM PAID")
            selected = next((v for v in paid_headers if v in labels), None)
            if selected is not None:
                cm.mapping["total_claims"] = labels.index(selected)
                cm.total_fallback = ""
                source_columns = {
                    "source_paid": ("AMOUNT PAID", "CLAIM PAID"),
                    "source_paid_total": ("TOTAL CLAIMS PAID", "TOTAL AMOUNT PAID"),
                    "source_total": ("TOTAL CLAIMS PAID", "TOTAL AMOUNT PAID"),
                    "source_reserve": ("LOSS RESERVE", "RESERVE"),
                    "source_reserve_total": ("TOTAL RESERVE AMOUNT",),
                    "source_fees": ("ADJUSTER FEE PAID", "ADJUSTERS FEE", "ADJUSTER OTHER FEE"),
                    "sum_insured": ("SUM INSURED",),
                }
                for field, choices in source_columns.items():
                    index = next((labels.index(v) for v in choices if v in labels), None)
                    if index is not None:
                        cm.mapping[field] = index
                cm.tie_log.append(f"HEIRS paid amount: exact {selected}; reserve excluded")
        return cm

    def resolve_claims_class(self, *, row, cmap, sheet_hint, section, **context):
        def value(field):
            index = cmap.get(field)
            return clean_text(row[index]) if index is not None and index < len(row) else ""
        parent = value("class")
        subclass = value("subclass")
        return self._resolve_class(parent or section or sheet_hint, subclass,
                                   "column" if parent else ("banner" if section else "sheet"), **context)

    def check_claims_row(self, claim, *, row, cmap, exceptions, path, sheet, excel_row):
        if cmap.get("source_paid") is None and cmap.get("source_paid_total") is None:
            return
        def number(field):
            index = cmap.get(field)
            return parse_number(row[index]) if index is not None and index < len(row) else None
        claim.sum_insured = number("sum_insured")
        exceptions.append(ExceptionRecord(
            "INFO", "heirs_claim_amount_basis", path.name, sheet, excel_row,
            json.dumps({"reserve": number("source_reserve"), "paid": number("source_paid"),
                        "reserve_total": number("source_reserve_total"),
                        "fees": number("source_fees"), "source_total": number("source_total"),
                        "selected": claim.total_claims,
                        "selected_column": cmap.get("total_claims") + 1,
                        "rule": "Explicit paid column only; no reserve fallback or fee calculation"}),
        ))


class HeirsJomolaAdapter(_HeirsEvidenceAdapter):
    cedant = "HEIRS"
    broker = "JOMOLA"
    premium_layout_rules = HEIRS_JOMOLA_PREMIUM_RULES
    verified = False
    status_note = "HEIRS/JOMOLA: quarterly treaty cession workbooks; unverified"

    def premium_header_cells(self, raw_rows, header_at, header, group_row):
        header, group_row = super().premium_header_cells(raw_rows, header_at, header, group_row)
        header = list(header)
        modern_bands = {"RTN PPN%", "TREATY PPN%"}.issubset({normalize_header(v) for v in header})
        band = ""
        for i, value in enumerate(header):
            group = clean_text(group_row[i]) if group_row and i < len(group_row) else ""
            if group:
                band = {"ret": "RETENTION", "sur": "TREATY", "fac": "FACULTATIVE"}.get(
                    self.premium_layout_rules.band_kind(group), "")
            label = normalize_header(value)
            if modern_bands:
                label = {"SUM INSURED N": "SUM INSURED", "PREMIUM N": "PREMIUM",
                         "RTN PPN%": "RETENTION PPN%"}.get(label, label)
                header[i] = label or value
            # Q1's S/I and Prem abbreviations are meaningful inside the
            # explicit allocation group. Preserve that ownership before the
            # generic block detector can pair RTN% with gross amounts.
            if band:
                label = {"S I": "SUM INSURED", "PREM": "PREMIUM"}.get(label, label)
            if band and label in {"SUM INSURED", "PREMIUM", "%"}:
                header[i] = f"{band} {label}"
        return header, group_row

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        result = super().prepare_source_sheet(sheet, rows, path=path, exceptions=exceptions)
        from src.domain.cre_cleaner.adapters.heirs_blocks import prepare_claims_blocks
        result = prepare_claims_blocks(result, path=path, sheet=sheet, exceptions=exceptions)
        for i, row in enumerate(result[:-1]):
            following = result[i + 1]
            if not {"INSURED", "POLICY NUMBER", "PREMIUM"}.issubset({normalize_header(v) for v in following}):
                continue
            filled = [(j, clean_text(v)) for j, v in enumerate(row) if clean_text(v)]
            classes = [(j, text) for j, text in filled if banner_class_label(text)
                       and not self.premium_layout_rules.band_kind(text)]
            if len(classes) != 1:
                continue
            class_at, label = classes[0]
            if not all(j == class_at or parse_number(text) is not None or
                       self.premium_layout_rules.band_kind(text) for j, text in filled):
                continue
            # Preserve band ownership in the following header before exposing
            # the isolated section label in this parser-only copy.
            prepared, _ = self.premium_header_cells(result, i + 1, following, row)
            result[i + 1] = prepared
            result[i] = [value if j == class_at else None for j, value in enumerate(row)]
            exceptions.append(ExceptionRecord("INFO", "heirs_moved_section_banner", path.name,
                sheet, i + 1, f"Source section {label!r} recognized at column {class_at + 1}; limits and allocation headings preserved in parser header"))
        # Append explicit source UW evidence, without shifting original cells.
        premium_headers = [i for i, row in enumerate(result) if
            {"INSURED", "POLICY NUMBER", "PREMIUM"}.issubset({normalize_header(v) for v in row})]
        if premium_headers and not any(normalize_header(v) in {"UW YEAR", "UW YR", "UNDERWRITING YEAR"}
                                       for i in premium_headers for v in result[i]):
            width = max((i + 1 for row in result for i, v in enumerate(row) if clean_text(v)), default=0)
            current_year = None
            for index, row in enumerate(result):
                filled = [clean_text(v) for v in row if clean_text(v)]
                if len(filled) == 1 and banner_class_label(filled[0]):
                    match = re.search(r"\bUW\s*(?:YEAR|YR)?\s*[:=\-]?\s*((?:19|20)\d{2})\b", filled[0], re.I)
                    current_year = int(match.group(1)) if match else None
                row.extend([None] * max(0, width + 1 - len(row)))
                if index in premium_headers:
                    row[width] = "UNDERWRITING YEAR"
                elif current_year is not None and len(filled) > 3:
                    row[width] = current_year
                    exceptions.append(ExceptionRecord("INFO", "heirs_uw_from_banner", path.name,
                        sheet, index + 1, f"UW year {current_year} copied from explicit source section heading"))
        # The 2022 legacy paid layout has two unlabelled recovery cells after
        # TOTAL CLAIMS PAID. Expose their positions only for this full signature;
        # check_claims_row still validates each proportion/amount pair.
        signature = {0: "DATE OF LOSS", 3: "NAME OF INSURED CLAIMANT",
                     5: "NEW CLAIM NO", 6: "POLICY NO", 7: "PERIOD OF COVER",
                     9: "DESCRIPTION OF LOSS", 11: "AMOUNT PAID", 13: "TOTAL CLAIMS PAID"}
        for header_at, row in enumerate(result):
            if (len(row) >= 14 and all(normalize_header(row[i]) == label for i, label in signature.items())
                    and all(i >= len(row) or not clean_text(row[i]) for i in (14, 15))):
                # Readers may omit wholly empty trailing columns, including P
                # in the amount-only layout. Restore only the known logical
                # width in these parser copies; populated cells never move.
                row.extend([None] * max(0, 16 - len(row)))
                following = [list(data) + [None] * max(0, 16 - len(data))
                             for data in result[header_at + 1:]]
                # A verified pair establishes this physical recovery column
                # for amount-only records under the same legacy header.
                anchored = False
                for data in following:
                    if len(data) < 16 or not clean_text(data[3]) or not clean_text(data[6]):
                        continue
                    total, fraction, amount = (parse_number(data[i]) for i in (13, 14, 15))
                    if (total is not None and fraction is not None and amount is not None
                            and 0 < fraction <= 1 and amount != 0
                            and abs(amount - fraction * total) <= .02):
                        anchored = True
                        break
                transactions = [data for data in following if len(data) >= 16
                                and clean_text(data[3]) and clean_text(data[6])
                                and parse_number(data[13]) is not None]
                amounts = [parse_number(data[14]) for data in transactions if clean_text(data[14])]
                controls = [parse_number(data[14]) for data in following if len(data) >= 16
                            and not clean_text(data[3]) and not clean_text(data[6]) and parse_number(data[14]) is not None]
                amount_only = (not anchored and amounts and any(abs(a or 0) > 1 for a in amounts)
                    and all(not clean_text(data[15]) for data in transactions)
                    and all(parse_number(data[14]) is not None and abs(parse_number(data[14])) <= abs(parse_number(data[13])) + .02
                            for data in transactions if clean_text(data[14]))
                    and controls and (any(abs(sum(amounts) - c) <= .02 for c in controls)
                                      or abs(sum(amounts) - sum(controls)) <= .02))
                if amount_only:
                    row[14] = "HEIRS CONTROLLED RECOVERY"
                    exceptions.append(ExceptionRecord("INFO", "heirs_amount_only_recovery", path.name,
                        sheet, header_at + 1, "Unlabelled O recovery column validated against source footer control; percentages not inferred"))
                else:
                    row[14:16] = ["HEIRS UNLABELLED PPN", "HEIRS VERIFIED RECOVERY" if anchored else "HEIRS UNLABELLED RECOVERY"]
        return result

    def map_claims_columns(self, header, aliases, sheet_type=""):
        cm = super().map_claims_columns(header, aliases, sheet_type)
        labels = [normalize_header(v) for v in header]
        for field, choices in (("ppn_treaty", ("TREATY PPN%", "TREATY %")),
                               ("amount_treaty", ("TREATY RECOVERY",))):
            exact = next((label for label in choices if label in labels), None)
            if exact is not None:
                cm.mapping[field] = labels.index(exact)
        if sheet_type != "paid":
            return cm
        # Operational processing time is never an insurance coverage period.
        if cm.get("period") is not None and labels[cm.get("period")] == "CLAIM PROCESS PERIOD":
            cm.mapping.pop("period", None)
        if "START DATE" in labels and "END DATE" in labels:
            cm.mapping.pop("period", None)
            cm.mapping.update(period_from=labels.index("START DATE"), period_to=labels.index("END DATE"))
        if "NEW CLAIM NO" in labels:
            cm.mapping["claim_no"] = labels.index("NEW CLAIM NO")
            if "CLAIM NO" in labels:
                cm.mapping["jomola_old_claim"] = labels.index("CLAIM NO")
        for field, label in (("jomola_fraction", "HEIRS UNLABELLED PPN"),
                             ("jomola_recovery", "HEIRS UNLABELLED RECOVERY"),
                             ("jomola_recovery", "HEIRS VERIFIED RECOVERY"),
                             ("jomola_verified_recovery", "HEIRS VERIFIED RECOVERY"),
                             ("jomola_controlled_recovery", "HEIRS CONTROLLED RECOVERY"),
                             ("jomola_separate_recovery", "TTREATY RECOVERY")):
            if label in labels:
                cm.mapping[field] = labels.index(label)
        return cm

    def check_claims_row(self, claim, *, row, cmap, exceptions, path, sheet, excel_row):
        super().check_claims_row(claim, row=row, cmap=cmap, exceptions=exceptions,
                                 path=path, sheet=sheet, excel_row=excel_row)
        def value(field):
            index = cmap.get(field)
            return row[index] if index is not None and index < len(row) else None
        def note(code, detail, severity="INFO"):
            exceptions.append(ExceptionRecord(severity, code, path.name, sheet, excel_row, detail))
        if not claim.claim_no and cmap.get("jomola_old_claim") is not None:
            claim.claim_no = clean_text(value("jomola_old_claim"))
        controlled = parse_number(value("jomola_controlled_recovery"))
        if controlled is not None:
            claim.amount_treaty = controlled
            note("heirs_amount_only_recovery", "Source amount copied from controlled unlabelled recovery column; percentage remains blank")
        fraction = parse_number(value("jomola_fraction"))
        recovery = parse_number(value("jomola_recovery"))
        if fraction is not None or recovery is not None:
            if fraction is None and (recovery == 0 or cmap.get("jomola_verified_recovery") is not None):
                claim.amount_treaty = recovery
                note("heirs_unlabelled_recovery", "Source recovery retained in validated recovery column; missing proportion left blank")
            elif (fraction is not None and recovery is not None and 0 <= fraction <= 1
                    and claim.total_claims is not None
                    and abs(recovery - fraction * claim.total_claims) <= .02):
                claim.ppn_treaty, claim.amount_treaty = fraction * 100, recovery
                note("heirs_unlabelled_recovery", "Unlabelled O/P recovery validated against TOTAL CLAIMS PAID; proportion converted to percent points")
            else:
                note("heirs_unlabelled_recovery_unresolved", "Unlabelled recovery failed proportion × total-claims validation; allocation left unmapped", "WARN")
        separate = parse_number(value("jomola_separate_recovery"))
        if separate is not None:
            if claim.amount_treaty in (None, 0):
                claim.amount_treaty = separate
                note("heirs_separate_recovery", "TTREATY RECOVERY supplies treaty amount because TREATY is blank/zero; amounts are not added")
            elif abs(claim.amount_treaty - separate) > .02:
                note("heirs_recovery_conflict", f"TREATY={claim.amount_treaty} differs from TTREATY RECOVERY={separate}; primary amount retained for review", "WARN")
    sheet_type_rules = HEIRS_JOMOLA_SHEET_RULES


class HeirsHibAdapter(_HeirsEvidenceAdapter):
    cedant = "HEIRS"
    broker = "HIB"
    verified = False
    status_note = "HEIRS/HIB: quarterly returns; unverified"


class HeirsDirectAdapter(_HeirsEvidenceAdapter):
    cedant = "HEIRS"
    broker = "DIRECT"
    verified = False
    status_note = "HEIRS Direct: quarterly returns; unverified"
