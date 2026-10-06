"""Parse premium / claims sheets and merge monthly premiums for a quarter."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.domain.cre_cleaner.core.class_labels import (
    banner_class_label,
    is_fac_sheet_name,
    is_unresolved_class,
    normalize_class_label,
)
from src.domain.cre_cleaner.core.detect import (
    detect_sheet_type,
    find_header_row,
    class_from_sheet_name,
)
from src.domain.cre_cleaner.core.filters import (
    is_blank_row,
    is_nil_row,
    looks_like_total_row,
    looks_like_section_header,
    has_min_transaction_evidence,
)
from src.domain.cre_cleaner.io.excel import read_source_workbook, read_workbook_sheets
from src.domain.cre_cleaner.core.table_type import OTHER, UNKNOWN, TabType, classify_tab
from src.domain.cre_cleaner.core.map_columns import (
    CLAIMS_ALIASES,
    ColumnMap,
    cell,
    detect_premium_allocation_blocks,
    extra_alias_columns,
    map_simple_columns,
    merge_group_subheaders,
    _looks_like_allocation_subrow,
)
from src.domain.cre_cleaner.models import (
    PremiumRow,
    ClaimsRow,
    AuditMeta,
    ExceptionRecord,
    SourceAuditRecord,
)
from src.domain.cre_cleaner.core.normalize import (
    as_text_id,
    clean_text,
    currency_code,
    currency_codes,
    currency_filter_code,
    prefer_named_over_fcy,
    parse_number,
    parse_date,
    parse_period,
    normalize_header,
)
from src.domain.cre_cleaner.core.reconcile import row_amounts

_NOT_A_DATE = {"NIL", "N/A", "NA", "-", "—", "TBA", "TBC"}
_COPY_SHEET_RE = re.compile(r"^(?P<base>.*?)\s*\((?P<n>\d+)\)\s*$")


def _workbook_currency_filter_hint(
    sheets: Dict[str, List[List[Any]]],
) -> Optional[str]:
    """Single Currency Filter code shared across tabs in one workbook, if any.

    Some exports put ``Currency Filter ( NAIRA at 1 )`` on the premium tab
    while the claims tab title only says FOREIGN CURRENCY — without this hint
    the two tabs split into NGN vs FCY workbooks.
    """
    found: set = set()
    for rows in sheets.values():
        for row in rows[:30]:
            for cell_v in row:
                if isinstance(cell_v, str):
                    code = currency_filter_code(cell_v)
                    if code:
                        found.add(code)
    if len(found) == 1:
        return next(iter(found))
    return None


def _sheet_base_name(sn: str) -> str:
    m = _COPY_SHEET_RE.match(str(sn).strip())
    return (m.group("base") if m else str(sn)).strip()


def _is_duplicate_copy_sheet(sn: str, all_names: Sequence[str]) -> bool:
    """True for ``CLAIMS RECOVERY (2)`` when ``CLAIMS RECOVERY`` (or another copy) exists."""
    m = _COPY_SHEET_RE.match(str(sn).strip())
    if not m:
        return False
    base = m.group("base").strip().casefold()
    for other in all_names:
        if other == sn:
            continue
        if _sheet_base_name(other).casefold() == base:
            return True
    return False


def _adapter_rules(adapter: Any):
    """(sheet-type rules, table vocab, class map) of the adapter; generic when
    no adapter is given. Cedant rules only ever come from the adapter."""
    if adapter is None:
        return None, None, None
    cmap = adapter.class_map() if hasattr(adapter, "class_map") else None
    return getattr(adapter, "sheet_type_rules", None), getattr(adapter, "table_vocab", None), cmap


def _uw_year_fallback(adapter: Any, uw_col: Optional[int], uw: Any, period_from: Any,
                      exceptions: List[ExceptionRecord], path: Path, sheet: str,
                      excel_row: int, kind: str) -> Any:
    """UW-year start-date fallback — adapter setting, OFF by default.

    The source UW column is always copied when the tab has one (all
    cedants). Only when the tab has *no* UW column AND the adapter setting
    ``uw_year_from_start_date`` is on for the run period is the UW year taken
    from the year of the policy start date (FROM); every derived value is
    flagged ``uw_year_from_start_date`` in the exceptions sidecar.
    """
    if uw_col is not None or adapter is None or not hasattr(adapter, "setting"):
        return uw
    if not adapter.setting("uw_year_from_start_date"):
        return uw
    year = getattr(period_from, "year", None)
    if not isinstance(year, int):
        return uw
    exceptions.append(ExceptionRecord(
        "INFO", "uw_year_from_start_date", path.name, sheet, excel_row,
        f"{kind}: no UW YEAR column — UW year {year} taken from policy start date "
        f"{period_from:%Y-%m-%d} (derived, approved fallback)",
    ))
    return year


def _row_list(rows: List[int]) -> str:
    """Compact 'r5-r9, r12' list of source rows."""
    out, start, prev = [], None, None
    for r in rows:
        if start is None:
            start = prev = r
        elif r == prev + 1:
            prev = r
        else:
            out.append(f"r{start}" if start == prev else f"r{start}-r{prev}")
            start = prev = r
    if start is not None:
        out.append(f"r{start}" if start == prev else f"r{start}-r{prev}")
    return ", ".join(out)


def _tie_note(ties: List[str]) -> str:
    """Source-audit note recording every column tie decision (alias-list
    order, then leftmost column)."""
    if not ties:
        return ""
    return "; column ties (alias order, then position): " + " | ".join(ties)


def _claims_header_with_subrow(
    raw_rows: List[List[Any]], header_at: int, merge: bool = False,
) -> Tuple[List[Any], int]:
    """Merge RETENTION|TREATY band + %/AMOUNT subheader into one virtual header
    (unless the adapter opts out with ``band_subrow_layout = False``).

    Returns ``(header_cells, data_start_index)``.
    """
    header = raw_rows[header_at]
    data_start = header_at + 1
    if not merge or header_at + 1 >= len(raw_rows):
        return list(header), data_start
    sub = raw_rows[header_at + 1]
    if not _looks_like_allocation_subrow(sub):
        return list(header), data_start
    return merge_group_subheaders(header, sub), header_at + 2


def _premium_header_with_subrow(
    raw_rows: List[List[Any]], header_at: int, merge: bool = False,
) -> Tuple[List[Any], Optional[List[Any]], int]:
    """Merge premium band + SI/Premium/% subrow (unless the adapter opts out
    with ``band_subrow_layout = False``). Returns (header, group_row, data_start)."""
    header = raw_rows[header_at]
    group_row = raw_rows[header_at - 1] if header_at > 0 else None
    data_start = header_at + 1
    if not merge or header_at + 1 >= len(raw_rows):
        return list(header), group_row, data_start
    sub = raw_rows[header_at + 1]
    if not _looks_like_allocation_subrow(sub):
        return list(header), group_row, data_start
    # The found "header" is the band row; merge with subrow and keep prior row as group.
    return merge_group_subheaders(header, sub), group_row, header_at + 2


def _load_source(path: Path) -> Tuple[Dict[str, List[List[Any]]], List[ExceptionRecord], Dict[str, int]]:
    """Read a source workbook without hidden sheets/rows; hidden content is
    logged, and hidden data rows are counted per sheet for reconciliation."""
    sheets, notes = read_source_workbook(path)
    exc = [ExceptionRecord(sev, reason, path.name, sn, row, detail)
           for sev, reason, sn, row, detail in notes]
    hidden_count: Dict[str, int] = defaultdict(int)
    for _sev, reason, sn, _row, _detail in notes:
        if reason == "hidden_row_skipped":
            hidden_count[sn] += 1
    return sheets, exc, hidden_count


class _SheetCurrency:
    """Currency for one source sheet.

    Priority (Cleaning Manual: copy source figures; never invent currency):
      1. ``Currency Filter ( NAIRA … )`` / similar in sheet headers
      2. Named currencies in header/title text (FCY dropped when a named code
         is also present — some titles say FOREIGN CURRENCY even for Naira)
      3. Sheet name → file name → folder name
      4. Workbook-level Currency Filter hint from a sibling sheet
      5. Default NGN

    A CURRENCY column on the row still wins per ``for_row``.
    """

    def __init__(
        self,
        path: Path,
        sheet: str,
        top_rows: Sequence[Sequence[Any]],
        exceptions: List[ExceptionRecord],
        *,
        file_currency_hint: Optional[str] = None,
    ):
        self.path, self.sheet, self.exceptions = path, sheet, exceptions
        self.code, self.source = "NGN", "default"
        header_cells = [c for r in top_rows for c in r if isinstance(c, str)]

        filter_codes = {currency_filter_code(t) for t in header_cells}
        filter_codes.discard("")
        if len(filter_codes) == 1:
            self.code, self.source = filter_codes.pop(), "currency filter"
        elif len(filter_codes) > 1:
            exceptions.append(ExceptionRecord(
                "WARN", "currency_ambiguous", path.name, sheet, 0,
                f"currency filter names several currencies {sorted(filter_codes)}; "
                "checking header text / file name",
            ))

        if self.source == "default":
            levels = (
                ("header text", header_cells),
                ("sheet name", [sheet]),
                ("file name", [path.name]),
                ("folder name", [path.parent.name]),
            )
            for source, texts in levels:
                codes: set = set()
                for t in texts:
                    codes |= currency_codes(t)
                codes = prefer_named_over_fcy(codes)
                if len(codes) == 1:
                    self.code, self.source = codes.pop(), source
                    break
                if len(codes) > 1:
                    exceptions.append(ExceptionRecord(
                        "WARN", "currency_ambiguous", path.name, sheet, 0,
                        f"{source} names several currencies {sorted(codes)}; "
                        "checking the next level",
                    ))

        # Sibling sheet had an authoritative Currency Filter (e.g. premium tab
        # says NAIRA; claims tab only has the FOREIGN CURRENCY report title).
        if (
            self.code == "FCY"
            and file_currency_hint
            and file_currency_hint != "FCY"
        ):
            exceptions.append(ExceptionRecord(
                "INFO", "currency_workbook_filter", path.name, sheet, 0,
                f"{self.source} looked like FCY; using workbook Currency Filter "
                f"{file_currency_hint} from a sibling sheet",
            ))
            self.code, self.source = file_currency_hint, "workbook currency filter"

        if self.code == "FCY":
            exceptions.append(ExceptionRecord(
                "WARN", "currency_unspecified_foreign", path.name, sheet, 0,
                f"{self.source} says foreign currency without naming it; "
                "written to the FCY workbook",
            ))
        self.seen: set = set()
        self._bad_logged = False

    def for_row(self, raw: Any) -> str:
        text = clean_text(raw)
        code = currency_code(text) if text else ""
        if not code and text and parse_number(text) is None and not self._bad_logged:
            self._bad_logged = True
            self.exceptions.append(ExceptionRecord(
                "WARN", "currency_unrecognised", self.path.name, self.sheet, 0,
                f"CURRENCY value {text!r} not recognised; sheet currency {self.code} used",
            ))
        code = code or self.code
        self.seen.add(code)
        return code

    def audit_code(self) -> str:
        if len(self.seen) > 1:
            return "MIXED"
        return next(iter(self.seen)) if self.seen else self.code


class _SheetTotals:
    """Row sums of kept rows (per currency) plus the cedant's own total rows."""

    def __init__(self, main_metric: str):
        self.main = main_metric
        self.parsed: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.rows: Dict[str, int] = defaultdict(int)
        self.footer: Dict[str, float] = defaultdict(float)
        self._section_cum: Dict[str, float] = defaultdict(float)
        self._section_seg: Dict[str, float] = defaultdict(float)
        self.footer_cells: Dict[str, List[str]] = defaultdict(list)
        self._section_footers: List[Tuple[Dict[str, float], Dict[str, float], Dict[str, float]]] = []
        self._section_cells: List[Dict[str, str]] = []

    def add_row(self, row) -> None:
        ccy = row.audit.currency
        self.rows[ccy] += 1
        for k, v in row_amounts(row).items():
            self.parsed[ccy][k] += v
            self._section_cum[k] += v
            self._section_seg[k] += v

    def add_footer(self, values: Dict[str, float], cells: Optional[Dict[str, str]] = None) -> None:
        self._section_footers.append((values, dict(self._section_cum), dict(self._section_seg)))
        self._section_cells.append(dict(cells or {}))
        self._section_seg = defaultdict(float)

    def end_section(self) -> None:
        """A grand-total row (≈ everything above it) wins; otherwise subtotal
        rows are added up."""
        fs = self._section_footers
        cs = self._section_cells
        chosen: Dict[str, float] = {}
        chosen_cells: Dict[str, List[str]] = defaultdict(list)
        if len(fs) == 1:
            chosen = fs[0][0]
            for k, c in cs[0].items():
                chosen_cells[k].append(c)
        elif fs:
            grand = []
            for i, (vals, cum, seg) in enumerate(fs):
                key = self.main if self.main in vals else next(iter(vals))
                if abs(vals[key] - cum.get(key, 0.0)) < abs(vals[key] - seg.get(key, 0.0)):
                    grand.append(i)
            if grand:
                chosen = fs[grand[-1]][0]
                for k, c in cs[grand[-1]].items():
                    chosen_cells[k].append(c)
            else:
                acc: Dict[str, float] = defaultdict(float)
                for i, (vals, _c, _s) in enumerate(fs):
                    for k, v in vals.items():
                        acc[k] += v
                    for k, c in cs[i].items():
                        chosen_cells[k].append(c)
                chosen = acc
        for k, v in chosen.items():
            self.footer[k] += v
        for k, cells in chosen_cells.items():
            self.footer_cells[k].extend(cells)
        self._section_cum = defaultdict(float)
        self._section_seg = defaultdict(float)
        self._section_footers = []
        self._section_cells = []

    def fill(self, rec: SourceAuditRecord) -> SourceAuditRecord:
        rec.parsed_totals = {c: dict(t) for c, t in self.parsed.items()}
        rec.parsed_rows = dict(self.rows)
        rec.footer_totals = dict(self.footer)
        rec.footer_cells = {k: ", ".join(v) for k, v in self.footer_cells.items() if v}
        return rec


def _footer_values(row: Sequence[Any], metric_cols: Dict[str, Optional[int]]) -> Dict[str, float]:
    out = {}
    for k, i in metric_cols.items():
        if i is None:
            continue
        v = parse_number(cell(row, i))
        if v is not None:
            out[k] = v
    return out


def _footer_cells(values: Dict[str, float], metric_cols: Dict[str, Optional[int]],
                  excel_row: int) -> Dict[str, str]:
    """{metric: cell reference} of a printed total row (0-based column indices)."""
    from openpyxl.utils import get_column_letter
    return {k: f"{get_column_letter(metric_cols[k] + 1)}{excel_row}"
            for k in values if metric_cols.get(k) is not None}


def _is_footer_row(row: Sequence[Any], insured: str, policy: str, values: Dict[str, float]) -> bool:
    """Cedant total row: no insured/policy, an amount in a mapped amount
    column, and either 'TOTAL' text or no words at all."""
    if insured or policy or not values:
        return False
    text = " ".join(clean_text(c).upper() for c in row if isinstance(c, str))
    return "TOTAL" in text or not any(ch.isalpha() for ch in text)


def _date_or_flag(raw: Any, field: str, flag) -> Any:
    d = parse_date(raw)
    text = clean_text(raw)
    if d is None and text and text.upper() not in _NOT_A_DATE:
        flag(field, text)
    return d


def _period_cells(row, cmap: ColumnMap, header: Sequence[Any], flag) -> Tuple[Any, Any]:
    """FROM/TO from a single PERIOD cell ('dd/mm/yyyy - dd/mm/yyyy') or FROM/TO
    columns. A PERIOD header spanning two cells (merged, blank header on the
    right) holds TO in the next column."""
    pi = cmap.get("period")
    if pi is None:
        return (
            _date_or_flag(cell(row, cmap.get("period_from")), "FROM", flag),
            _date_or_flag(cell(row, cmap.get("period_to")), "TO", flag),
        )
    raw = cell(row, pi)
    period_from, period_to = parse_period(raw)
    if period_from is None and clean_text(raw) and clean_text(raw).upper() not in _NOT_A_DATE:
        flag("PERIOD", clean_text(raw))
    if period_to is None and not clean_text(cell(header, pi + 1)):
        nxt = cell(row, pi + 1)
        if parse_date(nxt) is not None:
            period_to = parse_date(nxt)
    return period_from, period_to


def _first_data_row(raw_rows: List[List[Any]]) -> Optional[int]:
    for i, r in enumerate(raw_rows[:60]):
        filled = sum(1 for c in r if clean_text(c))
        nums = sum(1 for c in r if isinstance(c, (int, float)) and not isinstance(c, bool))
        if filled >= 5 and nums >= 2:
            return i
    return None


def _positional_header(
    adapter: Any, sheet_type: str, raw_rows: List[List[Any]], path: Path, sn: str,
    exceptions: List[ExceptionRecord],
) -> Optional[Tuple[int, List[Any], Optional[List[Any]]]]:
    """(virtual header index, header, band row) when the sheet has no header
    row and the cedant adapter knows its fixed column layout."""
    if adapter is None or not hasattr(adapter, "synthetic_header"):
        return None
    first = _first_data_row(raw_rows)
    if first is None:
        return None
    layout = adapter.synthetic_header(sheet_type, raw_rows[first])
    if layout is None:
        return None
    exceptions.append(ExceptionRecord(
        "WARN", "header_missing_positional_layout", path.name, sn, first + 1,
        f"No {sheet_type} header row; {adapter.cedant} column layout assumed from row {first + 1} "
        "(first data row matched the expected shape) — please spot-check",
    ))
    # Virtual header index just above the first data row (may be -1 when data
    # starts at row 1). The synthetic header/band live in header_override, not
    # in the sheet.
    return first - 1, layout[0], layout[1]


def _repeated_headers(raw_rows: List[List[Any]], hdr_i: int) -> List[int]:
    """Header row plus later identical header rows (one block per class)."""
    first = [normalize_header(c) for c in raw_rows[hdr_i] if clean_text(c)]
    out = [hdr_i]
    for i in range(hdr_i + 1, len(raw_rows)):
        norms = [normalize_header(c) for c in raw_rows[i] if clean_text(c)]
        if len(norms) >= 4 and norms == first:
            out.append(i)
    return out


def _treaty_layer_note(path: Path, sn: str, primary: str, extras: Sequence[str]) -> ExceptionRecord:
    return ExceptionRecord(
        "WARN", "multiple_treaty_layers", path.name, sn, 0,
        f"{len(extras) + 1} treaty layers: {primary or 'TREATY'} → TREATY band; "
        f"{', '.join(extras)} kept per row outside the 18-column upload schema "
        "(per-layer totals in SUMMARY reconciliation)",
    )


def _premium_metric_cols(cmap: ColumnMap) -> Dict[str, Optional[int]]:
    cols = {"gross": cmap.get("gross_premium"), "ret": cmap.ret_prem,
            "treaty": cmap.sur_prem, "fac": cmap.fac_prem}
    for label, _p, _s, pr in cmap.extra_treaty_blocks:
        cols[f"layer:{label}"] = pr
    return cols


def _type_fields(tt: Optional[TabType]) -> Dict[str, Any]:
    if tt is None:
        return {}
    return dict(detected_type=tt.label, type_confidence=round(tt.confidence, 2),
                type_evidence=tt.audit_text()[:2000])


def _typed_sheet(path: Path, sn: str, raw_rows: List[List[Any]], legacy_st: str,
                 adapter: Any, exceptions: List[ExceptionRecord]) -> Tuple[str, TabType]:
    """Sheet type for one tab (IMPL-20260929-02, Manual C.5 / D.1).

    Content-typing adapters: the type comes from the tab's own tables
    (core.table_type); UNKNOWN goes to the exceptions sidecar with its
    evidence and is not loaded; OTHER (empty / summary / statement) is only
    logged. Other adapters keep the name rules; the content type is advisory
    and a disagreement is logged.
    """
    tt = classify_tab(path.name, sn, raw_rows, vocab=_adapter_rules(adapter)[1])
    content = tt.sheet_type
    loadable = {"premium", "paid", "outstanding"}
    first = next((t.header_row for t in tt.tables if t.header_row >= 0), -1) + 1
    if not getattr(adapter, "content_sheet_typing", False):
        if content in loadable and content != legacy_st:
            exceptions.append(ExceptionRecord(
                "INFO", "table_type_disagrees", path.name, sn, first,
                f"name rules say {legacy_st}; content says {tt.label}: {tt.audit_text()[:600]}",
            ))
        return legacy_st, tt
    if tt.label == UNKNOWN:
        exceptions.append(ExceptionRecord(
            "WARN", "table_type_unknown", path.name, sn, first,
            f"UNKNOWN table type — not loaded; conflicts: {'; '.join(tt.conflicts) or '-'}; "
            f"evidence: {tt.audit_text()[:600]} (name rules said {legacy_st})",
        ))
    elif tt.label == OTHER:
        if legacy_st in loadable:
            exceptions.append(ExceptionRecord(
                "INFO", "table_type_other", path.name, sn, first,
                f"OTHER (not loaded): {tt.audit_text()[:300]} (name rules said {legacy_st})",
            ))
    elif content != legacy_st:
        exceptions.append(ExceptionRecord(
            "INFO", "table_type_from_content", path.name, sn, first,
            f"{tt.label} from content (name rules said {legacy_st}): {tt.audit_text()[:600]}",
        ))
    return content, tt


def _audit_hidden_tabs(path: Path, exceptions: List[ExceptionRecord], adapter: Any,
                       source_month: str = "") -> List[SourceAuditRecord]:
    """Hidden sheets are never loaded; content-typing adapters still type
    them so the audit shows what each hidden tab holds."""
    if not getattr(adapter, "content_sheet_typing", False):
        return []
    names = [e.source_sheet for e in exceptions
             if e.reason == "hidden_sheet_skipped" and e.source_filename == path.name]
    if not names:
        return []
    try:
        allsh = read_workbook_sheets(path)
    except Exception:  # pragma: no cover - reader already succeeded once
        return []
    out = []
    for sn in names:
        rows = allsh.get(sn)
        if rows is None:
            continue
        tt = classify_tab(path.name, sn, rows, vocab=_adapter_rules(adapter)[1])
        n = sum(t.n_rows for t in tt.tables)
        if tt.label not in (OTHER,) and n:
            exceptions.append(ExceptionRecord(
                "WARN", "hidden_table_not_loaded", path.name, sn, 0,
                f"hidden sheet holds a {tt.label} table ({n} rows) — not loaded; {tt.audit_text()[:300]}",
            ))
        out.append(SourceAuditRecord(
            path.name, sn, "hidden_skip", 0, 0, 0, 0, source_month,
            "hidden sheet — typed for audit, not loaded", **_type_fields(tt),
        ))
    return out


def parse_premium_file(
    path: Path,
    source_month: str,
    *,
    include_fac: bool = False,
    adapter: Any = None,
) -> Tuple[List[PremiumRow], List[ExceptionRecord], List[SourceAuditRecord]]:
    path = Path(path)
    sheets, exceptions, hidden_count = _load_source(path)
    rows_out: List[PremiumRow] = []
    audit: List[SourceAuditRecord] = []
    file_ccy_hint = _workbook_currency_filter_hint(sheets)

    sheet_names = list(sheets.keys())
    sheet_rules, _vocab, class_map = _adapter_rules(adapter)
    for sn, raw_rows in sheets.items():
        if is_fac_sheet_name(sn) and not include_fac:
            audit.append(SourceAuditRecord(
                source_filename=path.name,
                source_sheet=sn,
                sheet_type="fac_skip",
                header_row=0,
                rows_read=0,
                rows_kept=0,
                rows_skipped=0,
                source_month=source_month,
                notes="Facultative sheet ignored (not a treaty class; use --include-fac)",
            ))
            continue
        if _is_duplicate_copy_sheet(sn, sheet_names):
            exceptions.append(ExceptionRecord(
                "INFO", "duplicate_sheet_skipped", path.name, sn, 0,
                f"Copy sheet skipped; using base tab {_sheet_base_name(sn)!r}",
            ))
            audit.append(SourceAuditRecord(
                source_filename=path.name,
                source_sheet=sn,
                sheet_type="skip",
                header_row=0,
                rows_read=0,
                rows_kept=0,
                rows_skipped=0,
                source_month=source_month,
                notes="duplicate copy sheet skipped",
            ))
            continue
        st, tt = _typed_sheet(path, sn, raw_rows, detect_sheet_type(sn, raw_rows[:15], sheet_rules),
                              adapter, exceptions)
        if st != "premium":
            note = "skipped non-premium sheet in premium file"
            if st in ("paid", "outstanding") and getattr(adapter, "content_sheet_typing", False):
                note = (f"{st} table in premium file — not loaded here (claims come from the "
                        "claims files; see secondary-source check)")
            audit.append(SourceAuditRecord(
                source_filename=path.name,
                source_sheet=sn,
                sheet_type=st,
                header_row=0,
                rows_read=0,
                rows_kept=0,
                rows_skipped=0,
                source_month=source_month,
                notes=note,
                **_type_fields(tt),
            ))
            continue

        hdr_i = find_header_row(raw_rows, "premium")
        header_override: Dict[int, Tuple[List[Any], Optional[List[Any]]]] = {}
        if hdr_i is None:
            positional = _positional_header(adapter, "premium", raw_rows, path, sn, exceptions)
            if positional:
                hdr_i = positional[0]
                header_override[hdr_i] = (positional[1], positional[2])
        if hdr_i is None:
            exceptions.append(ExceptionRecord(
                "WARN", "header_not_found", path.name, sn, 0,
                "Could not detect premium header row",
            ))
            audit.append(SourceAuditRecord(
                path.name, sn, "premium", 0, 0, 0, 0, source_month, "no header",
            ))
            continue

        class_hint = class_from_sheet_name(sn)
        ccy_top = max(hdr_i + 1, 1)
        ccy = _SheetCurrency(
            path, sn, raw_rows[: ccy_top], exceptions,
            file_currency_hint=file_ccy_hint,
        )
        totals = _SheetTotals("gross")
        # Positional layouts have no real header rows to re-detect.
        header_indices = [hdr_i] if header_override else _repeated_headers(raw_rows, hdr_i)
        banner_class = ""
        banner_raw = ""
        banners_seen: List[str] = []
        layer_labels: List[str] = []
        primary_label = ""
        kept = skipped = read_n = 0
        tie_notes: List[str] = []

        for hi, header_at in enumerate(header_indices):
            if header_at in header_override:
                header, group_row = header_override[header_at]
                data_start = header_at + 1
            else:
                header, group_row, data_start = _premium_header_with_subrow(
                    raw_rows, header_at, merge=bool(getattr(adapter, "band_subrow_layout", True)))
            if adapter is not None:
                cmap = adapter.map_premium_columns(header, group_row, path=path, sheet=sn,
                                                   exceptions=exceptions)
            else:
                cmap = detect_premium_allocation_blocks(header, group_row)
            for t in getattr(cmap, "tie_log", []):
                note = f"r{header_at + 1} {t}"
                if note not in tie_notes:
                    tie_notes.append(note)
            for label, *_cols in cmap.extra_treaty_blocks:
                if label not in layer_labels:
                    layer_labels.append(label)
            primary_label = primary_label or cmap.sur_label
            if cmap.ignored_blocks:
                exceptions.append(ExceptionRecord(
                    "WARN", "allocation_block_ignored", path.name, sn, max(header_at + 1, 1),
                    "Second RET/FAC block has no upload column: " + "; ".join(cmap.ignored_blocks),
                ))
            if cmap.borrowed_blocks:
                exceptions.append(ExceptionRecord(
                    "WARN", "treaty_columns_not_own", path.name, sn, max(header_at + 1, 1),
                    "Band SI/premium left blank rather than copied from another band's "
                    "columns: " + "; ".join(cmap.borrowed_blocks),
                ))
            end = header_indices[hi + 1] if hi + 1 < len(header_indices) else len(raw_rows)
            next_group_row = end - 1 if hi + 1 < len(header_indices) else None
            metric_cols = _premium_metric_cols(cmap)

            # Tab gives no class (combined '2nd surplus' tabs / one PREMIUM tab
            # with class blocks): take the class from the lone section-banner
            # row above the rows, else the first cell of the band row (manual:
            # source grouping > row class > section/sheet heading > mapping >
            # exception).
            if not class_hint:
                found = False
                stop = header_indices[hi - 1] if hi else -1
                scan_from = header_at if header_at in header_override else header_at - 1
                for bi in range(scan_from, max(stop, header_at - 11), -1):
                    if bi < 0 or bi >= len(raw_rows):
                        continue
                    ne = [clean_text(c) for c in raw_rows[bi] if clean_text(c)]
                    if len(ne) == 1:
                        lab = banner_class_label(ne[0])
                        if lab:
                            banner_class, banner_raw = lab, ne[0]
                            banners_seen.append(f"{ne[0]!r}@r{bi + 1}->{lab}")
                            found = True
                            break
                if not found and group_row and header_at not in header_override:
                    first_cell = next((clean_text(c) for c in group_row if clean_text(c)), "")
                    lab = banner_class_label(first_cell) if first_cell else ""
                    if lab:
                        banner_class, banner_raw = lab, first_cell
                        banners_seen.append(f"{first_cell!r}@r{header_at}->{lab}")

            insured_i = cmap.get("insured")
            policy_i = cmap.get("policy_no")

            for ridx in range(data_start, end):
                row = raw_rows[ridx]
                excel_row = ridx + 1  # 1-based
                if is_blank_row(row):
                    continue
                if ridx == next_group_row and all(parse_number(c) is None for c in row):
                    # Band-label row of the next header block, not data.
                    continue
                read_n += 1
                if not class_hint:
                    ne = [clean_text(c) for c in row if clean_text(c)]
                    if len(ne) == 1:
                        lab = banner_class_label(ne[0])
                        if lab:
                            # Section banner row: sets class, never output itself.
                            banner_class, banner_raw = lab, ne[0]
                            banners_seen.append(f"{ne[0]!r}@r{excel_row}->{lab}")
                            skipped += 1
                            continue
                if is_nil_row(row):
                    skipped += 1
                    continue
                insured = clean_text(cell(row, insured_i)) if insured_i is not None else ""
                policy = as_text_id(cell(row, policy_i)) if policy_i is not None else ""
                fvals = _footer_values(row, metric_cols)
                if _is_footer_row(row, insured, policy, fvals):
                    totals.add_footer(fvals, _footer_cells(fvals, metric_cols, excel_row))
                    skipped += 1
                    continue
                if looks_like_total_row(row, [insured_i, policy_i]):
                    if insured or policy:
                        exceptions.append(ExceptionRecord(
                            "WARN", "total_like_row_skipped", path.name, sn, excel_row,
                            f"Row has TOTAL-style text but also insured={insured!r} "
                            f"policy={policy!r}; skipped as a total row — please confirm",
                        ))
                    skipped += 1
                    continue
                if looks_like_section_header(row) and not insured:
                    skipped += 1
                    continue

                gross = parse_number(cell(row, cmap.get("gross_premium")))
                si = parse_number(cell(row, cmap.get("sum_insured")))

                if not has_min_transaction_evidence(insured, policy, gross, si):
                    # uncertain — log, do not silently drop if has some content
                    if insured or policy or gross is not None:
                        exceptions.append(ExceptionRecord(
                            "INFO", "insufficient_evidence_kept_out",
                            path.name, sn, excel_row,
                            f"insured={insured!r} policy={policy!r} gross={gross}",
                        ))
                    skipped += 1
                    continue

                def flag_date(field: str, text: str, _row=excel_row) -> None:
                    exceptions.append(ExceptionRecord(
                        "WARN", "date_unparseable", path.name, sn, _row,
                        f"PREMIUM {field}={text!r} is not a valid date — left blank",
                    ))

                period_from, period_to = _period_cells(row, cmap, header, flag_date)

                uw = cell(row, cmap.get("uw_year"))
                if isinstance(uw, float) and uw == int(uw):
                    uw = int(uw)
                elif isinstance(uw, str) and uw.strip().isdigit():
                    uw = int(uw.strip())
                uw = _uw_year_fallback(adapter, cmap.get("uw_year"), uw, period_from,
                                       exceptions, path, sn, excel_row, "PREMIUM")

                channel = ""
                if cmap.get("channel") is not None:
                    channel = clean_text(cell(row, cmap.get("channel")))

                row_class = ""
                row_class_raw = ""
                if cmap.get("class") is not None:
                    row_class_raw = clean_text(cell(row, cmap.get("class")))
                    if row_class_raw:
                        # Prefer a resolved Bisola label; keep raw if unknown so
                        # divert/group still has something placeable.
                        mapped = normalize_class_label(row_class_raw, class_map)
                        row_class = mapped if mapped and mapped != "Other" else row_class_raw

                effective_class = ""
                class_source = ""
                class_raw = ""
                if class_hint and not is_unresolved_class(class_hint):
                    effective_class, class_source, class_raw = class_hint, "sheet", sn
                elif row_class and not is_unresolved_class(row_class):
                    effective_class, class_source, class_raw = row_class, "column", row_class_raw
                elif banner_class and not is_unresolved_class(banner_class):
                    effective_class, class_source, class_raw = banner_class, "banner", banner_raw
                else:
                    effective_class = class_hint or row_class or banner_class
                    class_source = (
                        "sheet" if class_hint else ("column" if row_class else ("banner" if banner_class else ""))
                    )
                    class_raw = sn if class_hint else (row_class_raw or banner_raw)

                extra_layers = []
                for label, ppn_i, si_i, prem_i in cmap.extra_treaty_blocks:
                    layer = {
                        "layer": label,
                        "ppn": parse_number(cell(row, ppn_i)),
                        "si": parse_number(cell(row, si_i)),
                        "prem": parse_number(cell(row, prem_i)),
                    }
                    if any(layer[k] is not None for k in ("ppn", "si", "prem")):
                        extra_layers.append(layer)

                prow = PremiumRow(
                    policy_no=policy,
                    name_of_insured=insured,
                    channel=channel,
                    sub_channel="",  # TEMPLATE has SUB CHANNEL; source SUB CLASS is class-ish — leave blank per design unless CHANNEL present
                    underwriting_year=uw,
                    period_from=period_from,
                    period_to=period_to,
                    total_sum_insured=si,
                    mpl_pct=parse_number(cell(row, cmap.get("mpl"))),
                    gross_premium=gross,
                    ret_ppn=parse_number(cell(row, cmap.ret_ppn)),
                    ret_si=parse_number(cell(row, cmap.ret_si)),
                    ret_prem=parse_number(cell(row, cmap.ret_prem)),
                    sur_ppn=parse_number(cell(row, cmap.sur_ppn)),
                    sur_si=parse_number(cell(row, cmap.sur_si)),
                    sur_prem=parse_number(cell(row, cmap.sur_prem)),
                    fac_ppn=parse_number(cell(row, cmap.fac_ppn)),
                    fac_si=parse_number(cell(row, cmap.fac_si)),
                    fac_prem=parse_number(cell(row, cmap.fac_prem)),
                    extra_layers=extra_layers,
                    audit=AuditMeta(
                        source_month=source_month,
                        source_filename=path.name,
                        source_sheet=sn,
                        source_row=excel_row,
                        class_hint=effective_class,
                        class_source=class_source,
                        class_label_raw=class_raw,
                        currency=ccy.for_row(cell(row, cmap.get("currency"))),
                    ),
                )
                if adapter is not None:
                    adapter.check_premium_row(prow, exceptions=exceptions, path=path,
                                              sheet=sn, excel_row=excel_row)
                # If required field missing — leave blank, log, still include
                if not policy:
                    exceptions.append(ExceptionRecord(
                        "WARN", "missing_policy_no", path.name, sn, excel_row, insured,
                    ))
                rows_out.append(prow)
                totals.add_row(prow)
                kept += 1
            totals.end_section()

        if layer_labels:
            exceptions.append(_treaty_layer_note(path, sn, primary_label, layer_labels))
        notes = f"class_hint={class_hint}; currency={ccy.audit_code()} ({ccy.source})"
        if adapter is not None and hasattr(adapter, "settings_note"):
            notes += "; " + adapter.settings_note()
        notes += _tie_note(tie_notes)
        if len(header_indices) > 1:
            notes += f"; {len(header_indices)} header blocks"
        if banners_seen:
            notes += f"; section banners: {', '.join(banners_seen)}"
        if layer_labels:
            notes += f"; treaty layers: {primary_label or 'TREATY'} + {', '.join(layer_labels)}"
        if kept == 0 and read_n == 0:
            exceptions.append(ExceptionRecord(
                "WARN", "empty_premium_sheet", path.name, sn, (hdr_i + 1) if hdr_i is not None else 0,
                "Premium header found but no data rows — file may be an empty month workbook",
            ))
            notes = (notes + "; empty sheet").strip("; ")
        audit.append(totals.fill(SourceAuditRecord(
            source_filename=path.name,
            source_sheet=sn,
            sheet_type="premium",
            header_row=hdr_i + 1,
            rows_read=read_n,
            rows_kept=kept,
            rows_skipped=skipped,
            source_month=source_month,
            notes=notes,
            currency=ccy.audit_code(),
            hidden_rows=hidden_count.get(sn, 0),
            **_type_fields(tt),
        )))

    audit.extend(_audit_hidden_tabs(path, exceptions, adapter, source_month))
    return rows_out, exceptions, audit


def merge_monthly_premiums(
    month_files: Sequence[Tuple[int, Path, str]],
    *,
    include_fac: bool = False,
    adapter: Any = None,
) -> Tuple[List[PremiumRow], List[ExceptionRecord], List[SourceAuditRecord]]:
    """month_files: (month_num, path, month_label) in calendar order. A
    quarterly premium file is passed as one entry (month 0, label 'Qn')."""
    all_rows: List[PremiumRow] = []
    all_exc: List[ExceptionRecord] = []
    all_audit: List[SourceAuditRecord] = []
    for _m, path, label in month_files:
        try:
            rows, exc, audit = parse_premium_file(path, label, include_fac=include_fac, adapter=adapter)
            all_rows.extend(rows)
            all_exc.extend(exc)
            all_audit.extend(audit)
        except Exception as e:
            all_exc.append(ExceptionRecord(
                "ERROR",
                "premium_file_unreadable",
                source_filename=Path(path).name,
                detail=str(e),
            ))
            all_audit.append(SourceAuditRecord(
                source_filename=Path(path).name,
                source_sheet="",
                sheet_type="premium",
                header_row=0,
                rows_read=0,
                rows_kept=0,
                rows_skipped=0,
                source_month=label,
                notes=f"unreadable: {e}",
            ))
    return all_rows, all_exc, all_audit


# Exact header labels (normalized). A repeated header row carries these as whole
# cells; narrative text such as 'CLAIM NOTIFICATION - ... POLICY' does not.
_CLAIM_NO_LABELS = {"CLAIM NO", "CLAIM NUMBER", "CLAIMNUMBER"}
_POLICY_LABELS = {"POLICY NO", "POLICY NUMBER", "POLICY"}


def _has_claims_header_labels(row: Sequence[Any]) -> bool:
    """True when the row has a whole-cell CLAIM NO label and a POLICY label."""
    cells = {normalize_header(c) for c in row if c is not None and clean_text(c)}
    return bool(cells & _CLAIM_NO_LABELS) and bool(cells & _POLICY_LABELS)


def _is_claims_header_block_start(row: Sequence[Any]) -> bool:
    """Column-header row that starts a block of claims rows (any position in the tab)."""
    if not _has_claims_header_labels(row):
        return False
    norms = [normalize_header(c) for c in row if c is not None]
    joined = " ".join(norms)
    if not ("DATE" in joined or "LOSS" in joined or "S NO" in joined
            or "S/N" in joined.replace(" ", "")):
        return False
    return sum(1 for n in norms if n) >= 5


def _parse_claims_sheet(
    path: Path,
    sn: str,
    raw_rows: List[List[Any]],
    sheet_type: str,
    aliases: Optional[Dict[str, List[str]]] = None,
    hidden_rows: int = 0,
    adapter: Any = None,
    file_currency_hint: Optional[str] = None,
) -> Tuple[List[ClaimsRow], List[ExceptionRecord], SourceAuditRecord]:
    aliases = aliases or CLAIMS_ALIASES
    exceptions: List[ExceptionRecord] = []
    hdr_i = find_header_row(raw_rows, "outstanding" if sheet_type == "outstanding" else "paid")
    class_hint = class_from_sheet_name(sn)
    header_override: Dict[int, List[Any]] = {}

    if hdr_i is None:
        positional = _positional_header(adapter, sheet_type, raw_rows, path, sn, exceptions)
        if positional:
            hdr_i = positional[0]
            header_override[hdr_i] = positional[1]

    if hdr_i is None:
        exceptions.append(ExceptionRecord(
            "WARN", "header_not_found", path.name, sn, 0, f"type={sheet_type}",
        ))
        return [], exceptions, SourceAuditRecord(
            path.name, sn, sheet_type, 0, 0, 0, 0, "", "no header", hidden_rows=hidden_rows,
        )

    # Some sheets repeat headers (e.g. FAC paid with monthly sections). Collect all header indices.
    # Positional layouts have no real header rows to re-detect.
    if header_override:
        header_indices = [hdr_i]
    else:
        # ALL header blocks in the tab, including blocks ABOVE the best-scoring
        # header find_header_row picked (2021 Q2 '2ND SURPLUS TREATY': the Fire
        # block at r3 was never parsed because the Engineering header at r13
        # scored higher).
        header_indices = sorted({hdr_i} | {
            i for i in range(len(raw_rows))
            if i != hdr_i and _is_claims_header_block_start(raw_rows[i])
        })
    earlier_blocks = [i + 1 for i in header_indices if i < hdr_i]

    ccy = _SheetCurrency(
        path, sn, raw_rows[: max(hdr_i + 1, 1)], exceptions,
        file_currency_hint=file_currency_hint,
    )
    totals = _SheetTotals("total")
    layer_labels: List[str] = []
    primary_label = ""
    rows_out: List[ClaimsRow] = []
    kept = skipped = read_n = 0
    # Persist across repeated monthly headers (e.g. 2ND SURPLUS TREATY: FIRE banner
    # then several month headers without re-stating the class).
    section_class = class_hint

    _SECTION_BANNER = {
        "FIRE", "ENGINEERING", "ENG", "MARINE HULL", "MARINE CARGO", "MARINE",
        "MISCELLANEOUS", "MISC", "BOND", "MOTOR", "ACCIDENT",
    }
    _SECTION_MAP = {
        "ENG": "ENGINEERING",
        "MISC": "MISCELLANEOUS ACCIDENT",
        "MISCELLANEOUS": "MISCELLANEOUS ACCIDENT",
        "MARINE": "MARINE",
    }

    banners_seen: List[str] = []
    section_raw = ""
    tie_notes: List[str] = []
    ppn_calc_rows: List[int] = []

    def _banner_class(cell_text: str) -> str:
        if not class_hint:
            # Tab gives no class (e.g. '2ND SURPLUS TREATY'): keyword banner
            # match, incl. 'FIRE PAID CLAIM', 'ENGINERRING', 'MARINE PAID CLAIM'.
            return banner_class_label(cell_text)
        b = cell_text.upper().strip()
        if b in _SECTION_BANNER:
            mapped = _SECTION_MAP.get(b, b)
            # Bare MARINE on a MARINE HULL / MARINE CARGO tab must not wipe the
            # more specific sheet class (rows would then fail as unresolved).
            if is_unresolved_class(mapped) and not is_unresolved_class(class_hint):
                return ""
            return mapped
        return ""

    for hi, header_at in enumerate(header_indices):
        if header_at in header_override:
            header = header_override[header_at]
            data_start = header_at + 1
        else:
            header, data_start = _claims_header_with_subrow(
                raw_rows, header_at,
                merge=bool(getattr(adapter, "band_subrow_layout", True)))
        if adapter is not None and hasattr(adapter, "map_claims_columns"):
            cmap = adapter.map_claims_columns(header, aliases)
        else:
            cmap = map_simple_columns(header, aliases)
        for t in getattr(cmap, "tie_log", []):
            note = f"r{header_at + 1} {t}"
            if note not in tie_notes:
                tie_notes.append(note)
        end = header_indices[hi + 1] if hi + 1 < len(header_indices) else len(raw_rows)
        insured_i = cmap.get("insured")
        policy_i = cmap.get("policy_no")
        extra_cols = extra_alias_columns(
            header, aliases["amount_treaty"], list(cmap.mapping.values()),
        ) + list(getattr(cmap, "extra_amount_columns", []))
        for _i, label in extra_cols:
            if label not in layer_labels:
                layer_labels.append(label)
        primary_label = primary_label or normalize_header(cell(header, cmap.get("amount_treaty")) or "")
        metric_cols: Dict[str, Optional[int]] = {
            "total": cmap.get("total_claims"), "ret": cmap.get("amount_ret"),
            "treaty": cmap.get("amount_treaty"), "fac": cmap.get("amount_fac"),
        }
        for i, label in extra_cols:
            metric_cols[f"layer:{label}"] = i
        # Look further back for a lone class banner above this header
        scan_from = header_at if header_at in header_override else header_at - 1
        for bi in range(scan_from, max(-1, header_at - 20), -1):
            if bi < 0:
                break
            ne = [clean_text(c) for c in raw_rows[bi] if clean_text(c)]
            if len(ne) == 1:
                mapped = _banner_class(ne[0])
                if mapped:
                    if section_class != mapped or section_raw != ne[0]:
                        banners_seen.append(f"{ne[0]!r}@r{bi + 1}->{mapped}")
                    section_class = mapped
                    section_raw = ne[0]
                    break
            # Stop if we hit a previous header row
            if bi in header_indices and bi != header_at:
                break

        for ridx in range(data_start, end):
            row = raw_rows[ridx]
            excel_row = ridx + 1
            if is_blank_row(row):
                continue
            ne = [clean_text(c) for c in row if clean_text(c)]
            if len(ne) == 1:
                mapped = _banner_class(ne[0])
                if mapped:
                    banners_seen.append(f"{ne[0]!r}@r{excel_row}->{mapped}")
                    section_class = mapped
                    section_raw = ne[0]
                    skipped += 1
                    continue
            # skip repeated header: whole-cell CLAIM NO + POLICY labels only
            # (a details cell like 'CLAIM NOTIFICATION - ...' is not a header)
            if _has_claims_header_labels(row):
                skipped += 1
                continue
            read_n += 1
            if is_nil_row(row):
                skipped += 1
                continue
            insured = clean_text(cell(row, insured_i)) if insured_i is not None else ""
            policy = as_text_id(cell(row, policy_i)) if policy_i is not None else ""
            fvals = _footer_values(row, metric_cols)
            if _is_footer_row(row, insured, policy, fvals):
                totals.add_footer(fvals, _footer_cells(fvals, metric_cols, excel_row))
                skipped += 1
                continue
            if looks_like_total_row(row, [insured_i, policy_i, cmap.get("claim_no")]):
                if insured or policy:
                    exceptions.append(ExceptionRecord(
                        "WARN", "total_like_row_skipped", path.name, sn, excel_row,
                        f"Row has TOTAL-style text but also insured={insured!r} "
                        f"policy={policy!r}; skipped as a total row — please confirm",
                    ))
                skipped += 1
                continue
            # month banners / lone dates
            non_empty = [c for c in row if clean_text(c)]
            if len(non_empty) <= 1:
                skipped += 1
                continue

            claim_no = as_text_id(cell(row, cmap.get("claim_no"))) if cmap.get("claim_no") is not None else ""
            total = parse_number(cell(row, cmap.get("total_claims")))
            amt_ret = parse_number(cell(row, cmap.get("amount_ret")))
            amt_tr = parse_number(cell(row, cmap.get("amount_treaty")))
            amt_fac = parse_number(cell(row, cmap.get("amount_fac")))

            if not has_min_transaction_evidence(insured, policy, total, amt_ret if amt_ret is not None else amt_tr):
                if insured or policy or claim_no:
                    exceptions.append(ExceptionRecord(
                        "INFO", "insufficient_evidence_kept_out",
                        path.name, sn, excel_row,
                        f"insured={insured!r} claim={claim_no!r}",
                    ))
                skipped += 1
                continue

            # class: prefer column, else sheet hint
            class_name = ""
            class_source = ""
            class_raw = ""
            if cmap.get("class") is not None:
                class_name = clean_text(cell(row, cmap.get("class")))
                if class_name:
                    class_source, class_raw = "column", class_name
            if not class_name:
                class_name = section_class or class_hint
                if section_raw and class_name == section_class and class_name != class_hint:
                    class_source, class_raw = "banner", section_raw
                elif class_name:
                    class_source, class_raw = "sheet", sn

            def flag_date(field: str, text: str, _row=excel_row) -> None:
                exceptions.append(ExceptionRecord(
                    "WARN", "date_unparseable", path.name, sn, _row,
                    f"{sheet_type.upper()} {field}={text!r} is not a valid date — left blank",
                ))

            period_from, period_to = _period_cells(row, cmap, header, flag_date)

            uw = cell(row, cmap.get("uw_yr"))
            if isinstance(uw, float) and uw == int(uw):
                uw = int(uw)
            uw = _uw_year_fallback(adapter, cmap.get("uw_yr"), uw, period_from,
                                   exceptions, path, sn, excel_row, sheet_type.upper())

            details = ""
            if cmap.get("details") is not None:
                details = clean_text(cell(row, cmap.get("details")))

            # Cedant-specific column-swap repair lives in the adapter.
            if adapter is not None and hasattr(adapter, "fix_claims_uw_details"):
                fixed = adapter.fix_claims_uw_details(uw, details)
                if fixed is not None:
                    exceptions.append(ExceptionRecord(
                        "INFO", "uw_year_details_swapped", path.name, sn, excel_row,
                        f"UW YR held loss text; DETAILS held year {details.strip()} — swapped",
                    ))
                    uw, details = fixed

            # Source proportions are copied as-is (blank when the source has
            # none). Only an adapter may derive them (derive_claims_ppn),
            # and every derived value is flagged 'Calculated' (Manual Claims 8).
            # Source PPN columns (when the adapter mapped them) are copied as-is.
            ppns = {k: (parse_number(cell(row, cmap.get(f"ppn_{k}")))
                        if cmap.get(f"ppn_{k}") is not None else None)
                    for k in ("ret", "treaty", "fac")}
            derive = (adapter.derive_claims_ppn
                      if adapter is not None and hasattr(adapter, "setting")
                      and adapter.setting("claims_ppn_calculated") else None)
            if derive is not None and all(cmap.get(f"ppn_{k}") is None for k in ppns):
                ppns = {k: derive(a, total) for k, a in
                        (("ret", amt_ret), ("treaty", amt_tr), ("fac", amt_fac))}
                if any(v is not None for v in ppns.values()):
                    ppn_calc_rows.append(excel_row)

            extra_layers = []
            for i, label in extra_cols:
                v = parse_number(cell(row, i))
                if v is not None:
                    extra_layers.append({"layer": label, "amount": v})

            crow = ClaimsRow(
                insured=insured,
                class_name=class_name,
                policy_no=policy,
                claim_no=claim_no,
                date_of_loss=_date_or_flag(cell(row, cmap.get("date_of_loss")), "DATE OF LOSS", flag_date),
                uw_yr=uw,
                period_from=period_from,
                period_to=period_to,
                total_claims=total,
                ppn_ret=ppns["ret"],
                amount_ret=amt_ret,
                ppn_treaty=ppns["treaty"],
                amount_treaty=amt_tr,
                ppn_fac=ppns["fac"],
                amount_fac=amt_fac,
                details=details,
                paid_date=_date_or_flag(cell(row, cmap.get("paid_date")), "PAYMENT DATE", flag_date),
                extra_layers=extra_layers,
                audit=AuditMeta(
                    source_filename=path.name,
                    source_sheet=sn,
                    source_row=excel_row,
                    class_hint=class_hint,
                    class_source=class_source,
                    class_label_raw=class_raw,
                    currency=ccy.for_row(cell(row, cmap.get("currency"))),
                ),
            )
            rows_out.append(crow)
            totals.add_row(crow)
            kept += 1
        totals.end_section()

    if layer_labels:
        exceptions.append(_treaty_layer_note(path, sn, primary_label, layer_labels))
    notes = f"class_hint={class_hint}; currency={ccy.audit_code()} ({ccy.source})"
    if adapter is not None and hasattr(adapter, "settings_note"):
        notes += "; " + adapter.settings_note()
    if ppn_calc_rows:
        # Manual Claims step 8: derived proportions are flagged 'Calculated'
        # in the source-audit sidecar (rows listed).
        notes += (f"; PPN RET/TREATY/FAC Calculated (layer amount / total claims; source has "
                  f"no proportion columns) on {len(ppn_calc_rows)} rows: "
                  + _row_list(ppn_calc_rows))
    notes += _tie_note(tie_notes)
    if banners_seen:
        notes += f"; section banners: {', '.join(banners_seen)}"
    if layer_labels:
        notes += f"; extra treaty layers: {', '.join(layer_labels)}"
    if earlier_blocks:
        notes += (f"; header blocks above r{hdr_i + 1} also parsed: "
                  f"{', '.join(f'r{r}' for r in earlier_blocks)}")
    return rows_out, exceptions, totals.fill(SourceAuditRecord(
        source_filename=path.name,
        source_sheet=sn,
        sheet_type=sheet_type,
        header_row=(hdr_i + 1) if hdr_i is not None else 0,
        rows_read=read_n,
        rows_kept=kept,
        rows_skipped=skipped,
        source_month="",
        notes=notes,
        currency=ccy.audit_code(),
        hidden_rows=hidden_rows,
    ))


def parse_claims_file(
    path: Path,
    *,
    include_fac: bool = False,
    adapter: Any = None,
) -> Tuple[List[ClaimsRow], List[ClaimsRow], List[ExceptionRecord], List[SourceAuditRecord]]:
    """Return (paid_rows, outstanding_rows, exceptions, audit). Never merge paid+OST."""
    path = Path(path)
    sheets, exceptions, hidden_count = _load_source(path)
    aliases = adapter.claims_aliases() if adapter is not None else CLAIMS_ALIASES
    paid: List[ClaimsRow] = []
    ost: List[ClaimsRow] = []
    audit: List[SourceAuditRecord] = []
    file_ccy_hint = _workbook_currency_filter_hint(sheets)

    sheet_names = list(sheets.keys())
    sheet_rules = _adapter_rules(adapter)[0]
    for sn, raw_rows in sheets.items():
        if is_fac_sheet_name(sn) and not include_fac:
            audit.append(SourceAuditRecord(
                path.name, sn, "fac_skip", 0, 0, 0, 0, "",
                "Facultative sheet ignored (not a treaty class; use --include-fac)",
            ))
            continue
        if _is_duplicate_copy_sheet(sn, sheet_names):
            exceptions.append(ExceptionRecord(
                "INFO", "duplicate_sheet_skipped", path.name, sn, 0,
                f"Copy sheet skipped; using base tab {_sheet_base_name(sn)!r}",
            ))
            audit.append(SourceAuditRecord(
                path.name, sn, "skip", 0, 0, 0, 0, "", "duplicate copy sheet skipped",
            ))
            continue
        st = detect_sheet_type(sn, raw_rows[:20], sheet_rules)
        if st == "skip":
            # statement sheet etc.
            name_u = normalize_header(sn)
            if "STATEMENT" in name_u and not getattr(adapter, "content_sheet_typing", False):
                audit.append(SourceAuditRecord(
                    path.name, sn, "skip", 0, 0, 0, 0, "", "statement/cover skipped",
                ))
                continue
            # try content
            st2 = detect_sheet_type(sn, raw_rows[:30], sheet_rules)
            st = st2
        st, tt = _typed_sheet(path, sn, raw_rows, st, adapter, exceptions)
        if st == "premium":
            # FAC OBLIG UY premium cession inside claims workbook — skip for claims pipeline
            audit.append(SourceAuditRecord(
                path.name, sn, "premium", 0, 0, 0, 0, "",
                "premium cession sheet inside claims file — not merged into claims",
                **_type_fields(tt),
            ))
            continue
        if st not in {"paid", "outstanding"}:
            audit.append(SourceAuditRecord(
                path.name, sn, st, 0, 0, 0, 0, "",
                {"unknown": "UNKNOWN table type — not loaded (see exceptions)",
                 "other": "OTHER (empty / summary / statement) — not loaded"}.get(st, "unclassified skip"),
                **_type_fields(tt),
            ))
            continue

        rows, exc, aud = _parse_claims_sheet(
            path, sn, raw_rows, st, aliases=aliases, hidden_rows=hidden_count.get(sn, 0),
            adapter=adapter, file_currency_hint=file_ccy_hint,
        )
        for k, v in _type_fields(tt).items():
            setattr(aud, k, v)
        exceptions.extend(exc)
        audit.append(aud)
        if st == "paid":
            paid.extend(rows)
        else:
            ost.extend(rows)

    audit.extend(_audit_hidden_tabs(path, exceptions, adapter))
    return paid, ost, exceptions, audit
