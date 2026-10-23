"""AXA Mansard (Direct) adapter — AXA rules only (IMPL-20260929-04).

AXA premium returns carry no PPN triples; bands are labelled columns
(OUR RETENTION SUM INSURED / OUR RETENTION PREMIUM / TREATY PREMIUM …) and
each band's share is a bare 'PCNT' column sitting *after* that band's
premium, so PCNT columns are assigned by position.

AXA claims (paid and outstanding, Excel and PDF-extracted 2020-2025):
  ... | Gross Loss | MANSARD Share | % | Treaty Share | % | FAC Share | % | ...
Gross Loss is the 100% claim. The retention share is headed MANSARD /
RETAINED / Retention Share; each bare '%' (percent points) belongs to the
Share column on its left. JLT / OBLIG / COINS / SBU shares are other
arrangements, never RET / TREATY / FAC. 'Class' holds the class; SubClass and
PRODUCT hold policy types.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.domain.cre_cleaner.adapters.base import QuarterlyWorkbookAdapter
from src.domain.cre_cleaner.core.map_columns import ColumnMap, PremiumLayoutRules, find_named_column
from src.domain.cre_cleaner.core.normalize import normalize_header

Band = Tuple[Optional[int], Optional[int], Optional[int]]

_BARE_PCT = re.compile(r"%\d*")
_OTHER_ARRANGEMENTS = ("JLT", "OBLIG", "COINS", "SBU", "YOA")
_SHARE_FIELDS = frozenset({
    "total_claims", "amount_ret", "amount_treaty", "amount_fac", "ppn_ret", "ppn_treaty", "ppn_fac",
})
_NOT_CLASS = frozenset({"SUBCLASS", "SUB CLASS", "PRODUCT", "AXA PRODUCT"})

AXA_CLAIMS_ALIAS_EXTRA: Dict[str, List[str]] = {
    "total_claims": ["GROSS LOSS"],
    "amount_ret": ["RETENTION SHARE", "MANSARD SHARE", "RETAINED SHARE"],
    "amount_fac": ["FAC SHARE"],
    "details": ["DESCRIPTION OF THE LOSS"],
    "ppn_ret": ["RETENTION SHARE PPN", "MANSARD SHARE PPN", "RETAINED SHARE PPN", "RETAINED%", "RETAINED PPN"],
    "ppn_treaty": ["TREATY SHARE PPN", "TREATY PPN"],
    "ppn_fac": ["FAC SHARE PPN", "FAC PPN"],
}


class AxaPremiumRules(PremiumLayoutRules):
    RET_PPN = ("OUR RETENTION PCNT", "RETENTION PPN", "PCNT")
    RET_SI = ("OUR RETENTION SUM INSURED", "RETENTION SUM INSURED")
    RET_PREM = ("OUR RETENTION PREMIUM", "RETENTION PREMIUM", "RETENTION")
    SUR_SI = ("TREATY SUM INSURED", "QUOTA SHARE SUM INSURED", "1ST SURPLUS SUM INSURED")
    SUR_PREM = ("TREATY PREMIUM", "QUOTA SHARE PREMIUM", "TREATY FIRST SURPLUS",
                "1ST SURPLUS", "1ST SURP", "TREATY")
    FAC_SI = ("FACULTATIVE SUM INSURED", "FAC SUM INSURED")
    FAC_PREM = ("FACULTATIVE PREMIUM", "FAC PREMIUM", "FACULTATIVE")

    def named_bands(self, norms: List[str]) -> Optional[Dict[str, Band]]:
        f = lambda needles: find_named_column(norms, needles)  # noqa: E731
        ret_ppn, ret_si, ret_prem = f(self.RET_PPN), f(self.RET_SI), f(self.RET_PREM)
        sur_ppn, sur_si, sur_prem = None, f(self.SUR_SI), f(self.SUR_PREM)
        fac_ppn, fac_si, fac_prem = None, f(self.FAC_SI), f(self.FAC_PREM)
        # PCNT columns sit after each band's premium — assign by position.
        pcnts = [i for i, n in enumerate(norms) if n == "PCNT"]
        if ret_prem is not None and sur_prem is not None and len(pcnts) >= 2:
            ret_ppn = next((i for i in pcnts if ret_prem < i < sur_prem), pcnts[0])
            sur_ppn = next((i for i in pcnts if i > sur_prem), pcnts[1])
            if fac_prem is not None:
                fac_ppn = next((i for i in pcnts if i > fac_prem), None)
        elif ret_prem is not None and pcnts:
            ret_ppn = ret_ppn or next((i for i in pcnts if i > ret_prem), None)
        return {"ret": (ret_ppn, ret_si, ret_prem), "sur": (sur_ppn, sur_si, sur_prem),
                "fac": (fac_ppn, fac_si, fac_prem)}

    def extra_named_layers(self, norms: List[str], cm: ColumnMap) -> None:
        # OBLIG TREATY as a further layer when TREATY is already taken.
        oblig_prem = next((i for i, n in enumerate(norms) if "OBLIG" in n and "PREMIUM" in n), None)
        oblig_si = next((i for i, n in enumerate(norms) if "OBLIG" in n and "SUM INSURED" in n), None)
        if oblig_prem is not None:
            cm.extra_treaty_blocks.append(("OBLIG TREATY", None, oblig_si, oblig_prem))


class AxaDirectAdapter(QuarterlyWorkbookAdapter):
    cedant = "AXA"
    broker = "DIRECT"
    verified = False
    status_note = (
        "AXA Mansard: quarterly premium/claims Excel + PDFs (via LlamaParse); "
        "claims checked against Bisola gold 2024-2025"
    )
    premium_layout_rules = AxaPremiumRules()
    claims_alias_extra = AXA_CLAIMS_ALIAS_EXTRA
    # Bisola's AXA gold books energy business as its own 'Energy' class.
    class_label_extra = {"ENERGY": "Energy"}
    # 2024 outstanding returns name the type only in the file name
    # ('Q1 2024 OUSTANDING CLAIMS BORDEREAU' / tab "RI OS MARCH '24 (NGN)").
    outstanding_by_file_name = True

    def reconcile_source_overlaps(self, result, source_paths):
        """Exclude only a proven repeated NGN Energy block across AXA files.

        General outstanding returns can already contain the NGN block supplied
        again in the Energy workbook, whose separate USD tab must still load.
        Compare entire multisets, including raw source evidence and multiplicity.
        """
        from collections import Counter, defaultdict
        from dataclasses import asdict
        import json
        from pathlib import Path
        from src.domain.cre_cleaner.io.excel import read_workbook_sheets
        from src.domain.cre_cleaner.models import ExceptionRecord

        paths = defaultdict(set)
        for path in source_paths:
            paths[Path(path).name].add(Path(path))
        headers = {}
        for audit in result.source_audit:
            key = (audit.source_filename, audit.source_sheet)
            headers[key] = max(headers.get(key, 0), audit.header_row)
        books = {}

        def raw_payload(row):
            audit = row.audit
            candidates = paths[audit.source_filename]
            header_row = headers.get((audit.source_filename, audit.source_sheet), 0)
            if len(candidates) != 1 or not header_row or audit.source_row <= header_row:
                return None
            path = next(iter(candidates))
            if path not in books:
                books[path] = read_workbook_sheets(path)
            sheet = books[path].get(audit.source_sheet, [])
            if audit.source_row > len(sheet) or header_row > len(sheet):
                return None
            header, values = sheet[header_row-1], sheet[audit.source_row-1]
            occurrences = Counter()
            payload = []
            for index, value in enumerate(values):
                label = normalize_header(header[index]) if index < len(header) else ''
                occurrences[label] += 1
                if value is not None and str(value).strip():
                    payload.append((label or f'UNLABELLED COLUMN {index+1}', occurrences[label], str(value).strip()))
            return tuple(sorted(payload))

        blocks, populations = defaultdict(list), Counter()
        for row in result.outstanding_rows:
            key = (row.audit.source_filename, row.audit.source_sheet)
            populations[key] += 1
            if row.audit.currency == 'NGN' and normalize_header(row.class_name) == 'ENERGY':
                blocks[key].append(row)
        owners, removed, exclusions = [], set(), []
        # The broader outstanding table controls over a repeated class-only tab.
        for key, rows in sorted(blocks.items(), key=lambda item:(-populations[item[0]], item[0])):
            parsed = []
            raw = []
            references = {(r.policy_no, r.claim_no) for r in rows if r.policy_no or r.claim_no}
            for row in rows:
                fields = asdict(row)
                fields.pop('audit', None)
                parsed.append(json.dumps(fields, default=str, sort_keys=True))
                raw.append(raw_payload(row))
            signature = Counter(parsed)
            raw_signature = Counter(raw) if all(v is not None for v in raw) else None
            match = next((owner for owner in owners if owner[0][0] != key[0]
                          and references & owner[3]), None)
            if match is None:
                owners.append((key, signature, raw_signature, references))
                continue
            owner, previous, previous_raw, _ = match
            if (signature != previous or raw_signature is None or raw_signature != previous_raw
                    or populations[key] != len(rows)):
                result.exceptions.append(ExceptionRecord(
                    'ERROR', 'source_overlap_ambiguous', key[0], key[1],
                    detail=f'NGN Energy outstanding blocks overlap between {owner[0]} / {owner[1]} and '
                           f'{key[0]} / {key[1]}, but a complete repeated class-only sheet '
                           'with matching raw values and multiplicity could not be confirmed. '
                           'Confirm the controlling source; no rows removed automatically.'))
                continue
            exclusions.append((key, rows, owner))
        if any(e.reason == 'source_overlap_ambiguous' for e in result.exceptions):
            return
        for key, rows, owner in exclusions:
            removed.update(id(row) for row in rows)
            amount = sum(row.total_claims or 0 for row in rows)
            detail = (f'{len(rows)} identical NGN Energy outstanding records already included from '
                      f'{owner[0]} / {owner[1]}; repeated block not loaded. Raw source values, parsed '
                      'fields and within-block multiplicity match; USD records are unaffected.')
            result.exceptions.append(ExceptionRecord(
                'WARN', 'axa_energy_block_not_loaded', key[0], key[1], min(r.audit.source_row for r in rows), detail=detail,
                amount=amount, currency='NGN'))
            for audit in result.source_audit:
                if (audit.source_filename, audit.source_sheet) == key and audit.sheet_type == 'outstanding':
                    audit.notes += '; axa_energy_block_not_loaded: ' + detail
                    audit.rows_skipped += len(rows)
                    audit.rows_kept = 0
                    audit.sheet_type = 'secondary:outstanding'
                    # Match the existing secondary-source audit convention:
                    # excluded sheets must not enter source reconciliation twice.
                    audit.parsed_totals, audit.parsed_rows = {}, {}
                    audit.footer_totals, audit.footer_cells = {}, {}
        result.outstanding_rows = [r for r in result.outstanding_rows if id(r) not in removed]

    def claims_exclude(self, field: str, norm: str) -> bool:
        if field == "class":
            return norm in _NOT_CLASS
        return field in _SHARE_FIELDS and norm.split(" ", 1)[0] in _OTHER_ARRANGEMENTS

    def claims_header_cells(
        self, header: Sequence[Any], band_row: Optional[Sequence[Any]],
        data_rows: Sequence[Sequence[Any]] = (), notes: Optional[list] = None,
    ) -> List[Any]:
        cells = list(header)
        renamed = []
        for i in range(1, len(cells)):
            n = normalize_header(cells[i]) if cells[i] is not None else ""
            left = normalize_header(cells[i - 1]) if cells[i - 1] is not None else ""
            if _BARE_PCT.fullmatch(n) and left.endswith(" SHARE"):
                cells[i] = f"{left} PPN"
                renamed.append(f"{n!r} after {left!r}")
        if renamed and notes is not None:
            notes.append(("INFO", "pct_column_by_position",
                          "Bare % columns read as the share on their left: " + "; ".join(renamed)))
        return cells
