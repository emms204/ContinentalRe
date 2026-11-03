"""Reusable column mechanics; company vocabulary remains in each adapter."""

from __future__ import annotations

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.core.detect import SheetTypeRules
from src.domain.cre_cleaner.core.map_columns import PremiumLayoutRules, is_share_header
from src.domain.cre_cleaner.core.normalize import normalize_header
from src.domain.cre_cleaner.core.table_type import TableVocab
import re


class NamedAllocationRules(PremiumLayoutRules):
    def block_for_ppn(self, pi, norms, si_idxs, prem_idxs):
        kind = self.band_kind(norms[pi])
        if kind:
            label = norms[pi].replace("PPN", "").rstrip(" %").strip()
            own_si = [
                i
                for i in si_idxs
                if abs(i - pi) <= 3 and norms[i].startswith(label + " ")
            ]
            own_prem = [
                i
                for i in prem_idxs
                if abs(i - pi) <= 3 and norms[i].startswith(label + " ")
            ]
            if label and len(own_si) == len(own_prem) == 1:
                return pi, own_si[0], own_prem[0]
            # Prefer immediately adjacent columns of this named block, rather
            # than a different block with the same broad treaty classification.
            si = [
                i
                for i in si_idxs
                if abs(i - pi) <= 2 and self.band_kind(norms[i]) == kind
            ]
            prem = [
                i
                for i in prem_idxs
                if abs(i - pi) <= 2 and self.band_kind(norms[i]) == kind
            ]
            if si and prem:
                return (
                    pi,
                    min(si, key=lambda i: abs(i - pi)),
                    min(prem, key=lambda i: abs(i - pi)),
                )
        return super().block_for_ppn(pi, norms, si_idxs, prem_idxs)


def qualify_bands(header, group_row, rules):
    names = [normalize_header(h) for h in header]
    groups = [normalize_header(g) for g in (group_row or [])]
    groups += [""] * max(0, len(names) - len(groups))
    first_si_band = next(
        (
            i
            for i, g in enumerate(groups[: len(names)])
            if rules.band_kind(g) and rules.looks_like_si(names[i])
        ),
        None,
    )
    ppn_first = (
        first_si_band is not None
        and first_si_band > 0
        and is_share_header(names[first_si_band - 1])
    )
    for i, g in enumerate(groups[: len(names)]):
        if (
            ppn_first
            and rules.band_kind(g)
            and i
            and is_share_header(names[i - 1])
            and rules.looks_like_si(names[i])
        ):
            groups[i - 1] = g
    active = ""
    for i, h in enumerate(names):
        if groups[i]:
            active = (
                groups[i]
                if rules.band_kind(groups[i])
                and (
                    is_share_header(h)
                    or rules.looks_like_si(h)
                    or rules.looks_like_prem(h)
                )
                else ""
            )
        if active and h and not rules.band_kind(h):
            if is_share_header(h):
                names[i] = active + " PPN"
            elif rules.looks_like_si(h) or rules.looks_like_prem(h):
                names[i] = active + " " + h
    return names


def split_period_columns(cm, header):
    i = cm.get("period")
    if (
        i is not None
        and i + 1 < len(header)
        and normalize_header(header[i + 1])
        in {"", "INSURANCE PERIOD", "PERIOD OF COVER", "PERIOD OF INSURANCE"}
    ):
        cm.mapping.pop("period", None)
        cm.mapping["period_from"] = i
        cm.mapping["period_to"] = i + 1
    return cm


class SourceLayoutAdapter(QuarterlyWorkbookAdapter):
    content_sheet_typing = True
    audit_hidden_contents = False
    strict_sheet_class_hints = True
    claims_resolved_sheet_class_first = True
    table_vocab = TableVocab()
    table_vocab.H_CLAIM = [
        (re.compile(r"\bOUTSTANDING (AMT|AMOUNT|RESERVE)\b"), 4),
        (re.compile(r"\bTOTAL CLAIMS OUTSTANDING\b"), 6),
        (re.compile(r"\bPERIOD OF COVER (FROM|TO)\b"), 2),
    ] + table_vocab.H_CLAIM
    table_vocab.VOCAB = table_vocab.H_CLAIM + table_vocab.VOCAB
    premium_layout_rules = NamedAllocationRules()
    sheet_type_rules = SheetTypeRules(
        ost_tokens=frozenset({"OST", "OS", "OSL", "OUTS", "OUTSTANDING"}),
        ost_slash=True,
        paid_without_claim=True,
        class_noise_words=frozenset(
            {
                "PREM",
                "PREMIUM",
                "BORD",
                "BORDEREAU",
                "BORDEREAUX",
                "PAID",
                "CLAIM",
                "CLAIMS",
                "OSL",
                "OST",
                "OUTSTANDING",
                "SURPLUS",
                "TREATY",
                "NGN",
                "USD",
                "NAIRA",
                "DOLLAR",
                "FINALIZED",
            }
        ),
    )
    status_note = (
        "Source-layout mapping; not yet checked against approved gold workbooks"
    )

    def read_source_workbook(self, path):
        # Typed standard reader for .xls/.xlsx/.xlsm; calamine only for
        # .xlsb/.ods. calamine's string reads turned number-formatted date
        # serials into text, dropped error cells and leading empty columns,
        # changed float digits, and had none of the wide-sheet speed-ups.
        from src.domain.cre_cleaner.io.excel import read_source_workbook

        return read_source_workbook(path)

    def premium_header_cells(self, raw_rows, header_at, header, group_row):
        return qualify_bands(header, group_row, self.premium_layout_rules), group_row

    def map_premium_columns(self, header, group_row, **kwargs):
        return split_period_columns(
            super().map_premium_columns(header, group_row, **kwargs), header
        )

    def claims_header_cells(self, header, band_row, data_rows=(), notes=None):
        result = qualify_bands(header, band_row, self.premium_layout_rules)
        active = ""
        for i, h in enumerate(header):
            g = normalize_header(band_row[i]) if band_row and i < len(band_row) else ""
            if g:
                active = g if self.premium_layout_rules.band_kind(g) else ""
            if active and normalize_header(h) in {"NGN", "USD", "AMOUNT", "AMT"}:
                result[i] = active + " AMOUNT"
        return result

    def claims_exclude(self, field, norm):
        # Generic AMOUNT must remain free for a type-specific total fallback.
        return (
            norm == "AMOUNT"
            and field in {"amount_ret", "amount_treaty", "amount_fac"}
            or field == "paid_date"
            and "DATE" not in norm
            and "DT" not in norm.split()
        )

    def map_claims_columns(self, header, aliases, sheet_type=""):
        cm = super().map_claims_columns(header, aliases, sheet_type)
        norms = [normalize_header(h) for h in header]
        for kind, field in [
            ("ret", "amount_ret"),
            ("treaty", "amount_treaty"),
            ("fac", "amount_fac"),
        ]:
            prefix = {"ret": "PPN RET", "treaty": "PPN TREATY", "fac": "PPN FAC"}[kind]
            for i, n in enumerate(norms[:-1]):
                if n.rstrip(" %") == prefix and norms[i + 1] == "AMOUNT":
                    cm.mapping[field] = i + 1
                    cm.mapping["ppn_" + kind] = i
            i = cm.get(field)
            if i is not None and i + 1 < len(norms) and is_share_header(norms[i + 1]):
                cm.mapping["ppn_" + kind] = i + 1
        return split_period_columns(cm, header)
