"""PRESTIGE Assurance via FAIR, IBN, SCIB, AFRO ASIAN, JB BODA, JOMOLA and KMD.

Outside KMD every bordereau — native workbook, scanned PDF or PDF-converted
workbook — is the same Prestige system report family ("PRESTIGE ASSURANCE /
19 Ligali Ayorinde"): Treaty Premium Bordereau, All Claims Paid and
Recoverable on Outstanding. Each allocation band is written '% | AMOUNT' in
the order QUOTA SHARE, RETENTION, SURPLUS, FAC after the gross column.

Like the AXA and CHI/SCIB adapters, one adapter reads both native workbooks and
converter output (``_Document`` / ``_Provenance`` / ``_Review`` sheets, one tab per
converter class, split/typo header text). PDFs are not read: only their
converted workbook, saved beside them under the same name. A converted
workbook is taken as correctly extracted; the row arithmetic and printed-total
checks only flag, they never change a value.

KMD sends its own Oil & Energy quota-share schedules (Prestige line split into
deductible, foreign treaty and local treaty) — ``PrestigeKmdAdapter``.
"""
from __future__ import annotations

import re
from collections import Counter
from src.domain.cre_cleaner.adapters.base import list_input_files, relative_label, years_in_text
from src.domain.cre_cleaner.adapters.source_layouts import SourceLayoutAdapter
from src.domain.cre_cleaner.adapters.workbook_rules import ControlledQuarterFiles, file_quarters, retype_tables
from src.domain.cre_cleaner.config import QUARTER_MONTHS
from src.domain.cre_cleaner.core.class_labels import is_unresolved_class, normalize_class_label
from src.domain.cre_cleaner.core.normalize import clean_text, normalize_header, parse_date, parse_number
from src.domain.cre_cleaner.models import ExceptionRecord

CONVERTER_SHEETS = {"_DOCUMENT", "_PROVENANCE", "_REVIEW"}
_GROSS = {"GROSSPREMIUM": "GROSS PREMIUM", "TOTALPAIDCLAIM": "TOTAL PAID CLAIM", "RESERVE": "RESERVE"}
# Columns before the gross column: compact prefix → canonical header.
_LEADING = (
    ("POLICY", "POLICY NO"), ("CLAIMNO", "CLAIM NO"), ("INSUREDNAME", "INSURED NAME"),
    ("RISK", "RISK CLASS"), ("CLASS", "CLASS"), ("START", "START DATE"), ("END", "END DATE"),
    ("LOSSDATE", "LOSS DATE"), ("PAYMENT", "PAYMENT DATE"), ("NOTIFY", "NOTIFY DATE"),
    ("LOSSDETAIL", "LOSS DETAILS"), ("SUMINSURED", "SUM INSURED"), ("CLAIMPAIDAMOUNT", "CLAIM PAID AMOUNT"),
    ("ADJUSTER", "ADJUSTER AMOUNT"), ("CESSION", "CESSION NO"), ("DEBIT", "DEBIT NOTE"),
    ("TOPLOCATION", "TOP LOCATION"),
)
# After the gross column: four bands, '% | AMOUNT' each.
_BANDS = (("QUOTA SHARE", ("QOU", "QUO")), ("RETENTION", ("RET",)), ("SURPLUS", ("SUR",)), ("FAC", ("FAC",)))
_PERIOD = re.compile(r"period\s+(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})|as at\s+(\d{2}/\d{2}/\d{4})", re.I)
# Branch / class / MMYY / P / serial, optional 'G/' group prefix and '/NN' endorsement.
_POLICY_SHAPE = re.compile(r"^(G/)?[A-Z0-9]{2,4}/[A-Z]{1,6}/\d{3,4}/P/\d{3,7}(/\d{1,6})?$")
_CLAIM_SHAPE = re.compile(r"^[A-Z0-9]{2,4}/[A-Z]{1,6}/\d{4}/\d{3,6}$")


def _compact(v) -> str:
    return re.sub(r"[^A-Z%]", "", str(v or "").upper())


def _num(v):
    x = parse_number(v)
    return float(x) if isinstance(x, (int, float)) else None


def canonical_prestige_header(cells, kind: str):
    """Canonical Prestige header for one source header row, or None.

    The gross column (GROSS PREMIUM / TOTAL PAID CLAIM / RESERVE) anchors the
    row; earlier columns are named by their keyword, and the columns after it
    are the four bands in fixed order — each renamed only when its keyword is
    the band expected at that position ('QOUTA SHARE QOUT A', 'RETEN TION',
    'FAC PERCE' …). Cells are renamed in place; nothing moves.
    kind: 'premium' names band amounts '<BAND> PREMIUM', claims '<BAND> AMOUNT'.
    """
    comp = [_compact(c) for c in cells]
    g = next((i for i, c in enumerate(comp) if c in _GROSS), None)
    if g is None or not any(c.startswith("POLICY") for c in comp[:g]):
        return None
    out = list(cells)
    seen_details = False
    for i, c in enumerate(comp[:g]):
        if not c:
            continue
        if c == "INSURED":
            # Outstanding 2025: SUM INSURED printed as a second 'INSURED'.
            out[i] = "SUM INSURED" if seen_details else "INSURED"
            continue
        name = next((n for p, n in _LEADING if c.startswith(p)), None)
        if name:
            out[i] = name
            seen_details = seen_details or name == "LOSS DETAILS"
    out[g] = _GROSS[comp[g]]
    amount = "PREMIUM" if kind == "premium" else "AMOUNT"
    k = g + 1
    for band, keys in _BANDS:
        for part in ("%", amount):
            while k < len(comp) and not comp[k] and any(comp[k + 1:]):
                k += 1  # blank spacer column (native workbooks)
            if k >= len(comp) or not comp[k]:
                return out  # a truncated last band: keep what was named
            if not any(key in comp[k] for key in keys):
                return None  # unexpected column at a band position: do not guess
            out[k] = f"{band} {part}"
            k += 1
    return out


def prestige_band_columns(header):
    """{band: (ppn col, amount col)} from a canonical header."""
    norms = [normalize_header(h) for h in header]
    out = {}
    for band, _keys in _BANDS:
        ppn = next((i for i, n in enumerate(norms) if n == f"{band} %"), None)
        amt = next((i for i, n in enumerate(norms) if n in (f"{band} PREMIUM", f"{band} AMOUNT")), None)
        if ppn is not None or amt is not None:
            out[band] = (ppn, amt)
    return out


def check_allocation(*, gross, bands, exceptions, path, sheet, excel_row, what):
    """Fallback checks (flag only): band % add to 100, each amount equals its
    % of the gross, and the amounts add to the gross."""
    if gross in (None, 0):
        return
    pcts = [p for p, _a in bands.values() if p is not None]
    amts = [a for _p, a in bands.values() if a is not None]
    tol = max(0.06, abs(gross) * 0.0001)
    issues = []
    if pcts and abs(sum(pcts) - 100) > 0.1:
        issues.append(f"band % add to {sum(pcts):.2f}, not 100")
    for name, (p, a) in bands.items():
        if p is not None and a is not None and abs(gross * p / 100 - a) > tol:
            issues.append(f"{name} {p}% of {gross:,.2f} is {gross * p / 100:,.2f}, source {a:,.2f}")
    if amts and len(amts) == len(bands) and abs(sum(amts) - gross) > tol * len(amts):
        issues.append(f"band amounts add to {sum(amts):,.2f}, {what} {gross:,.2f}")
    if issues:
        exceptions.append(ExceptionRecord(
            "WARN", "prestige_allocation_check", path.name, sheet, excel_row,
            "; ".join(issues) + " — source values kept (fallback check only)"))


class _PrestigeAdapter(ControlledQuarterFiles, SourceLayoutAdapter):
    cedant = "PRESTIGE"
    verified = False
    status_note = ("PRESTIGE: native and PDF-converted Prestige system reports; converted workbooks are "
                   "taken as extracted (allocation and printed-total checks flag only); the same report "
                   "is often forwarded by several brokers — do not load two partners for one quarter without review")
    claims_header_scan_all = True
    class_label_extra = {
        "FIRE/IAR": "Fire", "FIRE IAR": "Fire", "IAR": "Fire",
        "MISCELLANEOUS": "General Accident",
        # User decisions 2026-10-23: GIT and MEDICLAIM → General Accident.
        "GOODS IN TRANSIT": "General Accident", "GIT": "General Accident",
        "MEDICLAIM": "General Accident",
        "GEN": "General Accident",
        "PRESTIGE AGRIC": "Agriculture",
        "PVT STANDALONE": "Terrorism & PVT", "PVT ATTACHED TO FIRE": "Terrorism & PVT",
        "TERRORISM AND POLITICAL RISK": "Terrorism & PVT",
        "BOND QOUTA SHARE": "Bond",
    }
    class_label_exact_only = frozenset({"GIT", "GEN", "IAR"})
    premium_alias_extra = {"class": ["RISK CLASS"], "insured": ["INSURED NAME"], "debit_note": ["DEBIT NOTE"]}
    claims_alias_extra = {
        "total_claims": ["TOTAL PAID CLAIM", "RESERVE"],
        "details": ["LOSS DETAILS"],
        "paid_date": ["PAYMENT DATE"],
        "ppn_treaty": ["QUOTA SHARE %"], "amount_treaty": ["QUOTA SHARE AMOUNT", "SURPLUS AMOUNT"],
        "ppn_ret": ["RETENTION %"], "amount_ret": ["RETENTION AMOUNT"],
        "ppn_fac": ["FAC %"], "amount_fac": ["FAC AMOUNT"],
    }

    # --- discovery -------------------------------------------------------
    def _choose(self, files, role, year, quarter):
        """Prestige sends one report per class per quarter: every distinct
        workbook is read. Copies named '(1)', '(2)' … of one report that differ
        in content are flagged and only the controlling one is read."""
        groups = {}
        for p in files:
            stem = re.sub(r"\s*\(\d+\)\s*$|\s+copy$", "", p.stem, flags=re.I).strip().casefold()
            groups.setdefault(stem, []).append(p)
        chosen = []
        for group in groups.values():
            chosen += self._pick(group, role, quarter) if len(group) > 1 else group
        if len(chosen) > 1:
            self._note("INFO", f"{role}_files_additive", chosen[0].name,
                       f"Q{quarter} {year}: {[p.name for p in chosen]} all read (one Prestige report per class)")
        return chosen

    def discover_claims_files(self, raw_dir, year, quarter):
        # Claims-only reports pass through the same copy check as combined ones.
        files = super().discover_claims_files(raw_dir, year, quarter)
        return self._choose(files, "claims", year, quarter) if len(files) > 1 else files

    def _quarter_files(self, raw_dir, year, quarter):
        out = super()._quarter_files(raw_dir, year, quarter)
        for p in list_input_files(raw_dir, year):
            label = relative_label(raw_dir, p)
            if p.suffix.lower() in {".xlsx", ".xls", ".xlsm"} and not file_quarters(label) \
                    and (not years_in_text(p.name) or year in years_in_text(p.name)):
                self._note("INFO", "file_period_not_quarterly", p.name,
                           f"{label}: names no quarter (annual / half-year / as-at file); not placed in a quarter "
                           "— rows are not split by date")
        return out

    # --- source sheets ---------------------------------------------------
    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        key = normalize_header(sheet).replace(" ", "_")
        if sheet.upper() in CONVERTER_SHEETS or key in CONVERTER_SHEETS:
            self._read_converter_sheet(sheet.upper(), rows, path, exceptions)
            return []
        return self._canonical_headers(sheet, rows, path, exceptions)

    def _read_converter_sheet(self, name, rows, path, exceptions):
        exceptions.append(ExceptionRecord(
            "INFO", "converter_metadata_skipped", path.name, name, 0,
            "PDF converter metadata kept in the source; not read as transactions"))
        if name == "_REVIEW":
            codes = Counter(clean_text(r[1]) for r in rows[1:] if len(r) > 1
                            and clean_text(r[0]).upper() in {"WARNING", "ERROR"})
            if codes:
                exceptions.append(ExceptionRecord(
                    "WARN", "converter_review_warnings", path.name, name, 0,
                    "Converter review notes: " + ", ".join(f"{c}={n}" for c, n in sorted(codes.items()))
                    + "; workbook taken as extracted (see the _Review sheet)"))
        if name == "_DOCUMENT":
            year, quarter = getattr(self, "period", (None, None))
            for r in rows:
                m = _PERIOD.search(" ".join(clean_text(v) for v in r if v is not None))
                if not m or not year or not quarter:
                    continue
                dates = [parse_date(d) for d in m.groups() if d]
                months = {d.month for d in dates if d and d.year == year}
                if dates and not (months and months <= set(QUARTER_MONTHS[quarter])):
                    exceptions.append(ExceptionRecord(
                        "WARN", "converter_period_mismatch", path.name, name, 0,
                        f"Document period {m.group(0)!r} is not {year} Q{quarter}; rows read as filed — review"))

    def _canonical_headers(self, sheet, rows, path, exceptions):
        out, renamed, relabelled, last = None, 0, [], None
        for i, r in enumerate(rows):
            comp = [_compact(c) for c in r]
            kind = "premium" if "GROSSPREMIUM" in comp else "claims"
            canon = canonical_prestige_header(r, kind)
            if canon is None and last is not None and _mislabelled_continuation(r, last):
                relabelled.append(f"r{i + 1}: " + ", ".join(clean_text(c) for c in r if c is not None)[:160])
                canon = list(last)
            if canon is None:
                continue
            if canon != list(r):
                out = out or [list(x) for x in rows]
                out[i] = canon
                renamed += 1
            last = canon
        if renamed:
            exceptions.append(ExceptionRecord(
                "INFO", "prestige_header_canonicalized", path.name, sheet, 0,
                f"{renamed} header row(s) named to the Prestige report columns (no cell moved)"))
        for note in relabelled:
            exceptions.append(ExceptionRecord(
                "WARN", "converter_header_mislabelled", path.name, sheet, 0,
                "Header labels differ from the report but the identity columns and column count match the "
                f"table above, whose header is used: {note}"))
        return out or rows

    # --- columns ---------------------------------------------------------
    def map_premium_columns(self, header, group_row, **kwargs):
        cm = super().map_premium_columns(header, group_row, **kwargs)
        bands = prestige_band_columns(header)
        if bands:
            # Bands carry % and premium only; bind them by name rather than let
            # the SI/premium heuristics borrow the gross columns.
            (cm.ret_ppn, cm.ret_prem), cm.ret_si = bands.get("RETENTION", (None, None)), None
            (cm.sur_ppn, cm.sur_prem), cm.sur_si = bands.get("QUOTA SHARE", (None, None)), None
            (cm.fac_ppn, cm.fac_prem), cm.fac_si = bands.get("FAC", (None, None)), None
            cm.sur_label = "QUOTA SHARE"
            sp, sa = bands.get("SURPLUS", (None, None))
            cm.extra_treaty_blocks = [("SURPLUS", sp, None, sa)] if sa is not None or sp is not None else []
        return cm

    def claims_exclude(self, field, norm):
        if field == "total_claims" and norm in {"CLAIM PAID AMOUNT", "ADJUSTER AMOUNT"}:
            return True  # TOTAL PAID CLAIM is the gross; these are its parts
        return super().claims_exclude(field, norm)

    def map_claims_columns(self, header, aliases, sheet_type=""):
        cm = super().map_claims_columns(header, aliases, sheet_type)
        # '% | AMOUNT': each proportion sits BEFORE its amount (the shared rule
        # would pair an amount with the next band's proportion).
        bands = prestige_band_columns(header)
        for (band, _k), kind in zip(_BANDS, ("treaty", "ret", None, "fac")):
            ppn, amt = bands.get(band, (None, None))
            if kind and ppn is not None and amt is not None and cm.get("amount_" + kind) == amt:
                cm.mapping["ppn_" + kind] = ppn
        if bands.get("SURPLUS", (None, None))[0] is not None:
            # Read only by check_claims_row; the extra layer carries amounts.
            cm.mapping["surplus_ppn"] = bands["SURPLUS"][0]
        return cm

    # --- the same report pages in two files -------------------------------
    def reconcile_source_overlaps(self, result, source_paths):
        """A converted report split over two files can repeat whole class
        pages (FAIR 2025 Q1: MEDICLAIM in both RECOVERABLES 1 and 2). A sheet
        whose rows are exactly the rows of a sheet in another file — same
        identifiers, amounts and multiplicity — is read once (WARN); any
        partial overlap is left alone (Manual: delete only with evidence)."""
        from dataclasses import asdict
        import json
        for attr in ("premium_rows", "claims_rows", "outstanding_rows"):
            rows = getattr(result, attr)
            blocks = {}
            for r in rows:
                blocks.setdefault((r.audit.source_filename, r.audit.source_sheet), []).append(r)

            def signature(block):
                out = Counter()
                for r in block:
                    fields = asdict(r)
                    fields.pop("audit", None)
                    out[json.dumps(fields, default=str, sort_keys=True)] += 1
                return out
            kept, dropped = [], set()
            for key, block in blocks.items():
                sig = signature(block)
                twin = next((k for k, s in kept if k[0] != key[0] and s == sig), None)
                if twin is None:
                    kept.append((key, sig))
                    continue
                dropped.add(key)
                result.exceptions.append(ExceptionRecord(
                    "WARN", "prestige_repeated_pages_not_loaded", key[0], key[1],
                    min(r.audit.source_row for r in block),
                    f"{len(block)} rows repeat {twin[0]} / {twin[1]} exactly (same identifiers, amounts and "
                    "multiplicity); read once"))
            if dropped:
                setattr(result, attr, [r for r in rows
                                       if (r.audit.source_filename, r.audit.source_sheet) not in dropped])

    # --- fallback checks (flag only) -------------------------------------
    def check_premium_row(self, prow, *, exceptions, path, sheet, excel_row):
        bands = {"QUOTA SHARE": (_num(prow.sur_ppn), _num(prow.sur_prem)),
                 "RETENTION": (_num(prow.ret_ppn), _num(prow.ret_prem)),
                 "FAC": (_num(prow.fac_ppn), _num(prow.fac_prem))}
        for lay in prow.extra_layers:
            bands[str(lay.get("layer"))] = (_num(lay.get("ppn")), _num(lay.get("prem")))
        bands = {k: v for k, v in bands.items() if v != (None, None)}
        check_allocation(gross=_num(prow.gross_premium), bands=bands, exceptions=exceptions, path=path,
                         sheet=sheet, excel_row=excel_row, what="gross premium")
        self._check_identifier(prow.policy_no, _POLICY_SHAPE, "policy", exceptions, path, sheet, excel_row)

    def check_claims_row(self, claim, *, row, cmap, exceptions, path, sheet, excel_row):
        bands = {"QUOTA SHARE": (_num(claim.ppn_treaty), _num(claim.amount_treaty)),
                 "RETENTION": (_num(claim.ppn_ret), _num(claim.amount_ret)),
                 "FAC": (_num(claim.ppn_fac), _num(claim.amount_fac))}
        sp = cmap.get("surplus_ppn")
        surplus_ppn = _num(row[sp]) if sp is not None and sp < len(row) else None
        for lay in claim.extra_layers:
            bands[str(lay.get("layer"))] = (surplus_ppn, _num(lay.get("amount")))
        bands = {k: v for k, v in bands.items() if v != (None, None)}
        check_allocation(gross=_num(claim.total_claims), bands=bands, exceptions=exceptions, path=path,
                         sheet=sheet, excel_row=excel_row, what="gross claim")
        self._check_identifier(claim.policy_no, _POLICY_SHAPE, "policy", exceptions, path, sheet, excel_row)
        self._check_identifier(claim.claim_no, _CLAIM_SHAPE, "claim", exceptions, path, sheet, excel_row)

    def _check_identifier(self, value, shape, what, exceptions, path, sheet, excel_row):
        v = clean_text(value)
        if v and not shape.match(v):
            exceptions.append(ExceptionRecord(
                "WARN", "prestige_identifier_shape", path.name, sheet, excel_row,
                f"{what} number {v!r} does not have the Prestige shape; kept as in the source"))

    # --- classes ---------------------------------------------------------
    def _placed(self, label) -> bool:
        return bool(label) and not is_unresolved_class(label) \
            and normalize_class_label(label, self.class_map()) not in {"", "Other"}

    def resolve_premium_class(self, *, sheet_hint, section, raw, **context):
        # Manual order: printed heading (section), the report's class grouping
        # (tab), then the row's RISK CLASS. Bare 'Marine' stays unresolved
        # for review (user decision 2026-10-23).
        for label, source in ((section, "banner"), (sheet_hint, "sheet"), (raw, "column")):
            if self._placed(label):
                return label, source, label
        return raw or section or sheet_hint, "column" if raw else "sheet", raw or sheet_hint

    def resolve_claims_class(self, *, row, cmap, sheet_hint, section, **context):
        i = cmap.get("class")
        raw = clean_text(row[i]) if i is not None and i < len(row) else ""
        return self.resolve_premium_class(sheet_hint=sheet_hint, section=section, raw=raw, **context)


def _mislabelled_continuation(row, last) -> bool:
    """A later header row the converter labelled differently (page 9 of the
    2025 Q1 BORDEREAU: 'Comm % | Net % | VAT % …') but with the same identity
    columns and column count as the Prestige header above."""
    cells = [clean_text(c) for c in row]
    filled = [c for c in cells if c]
    if len(filled) != len([c for c in last if clean_text(c)]):
        return False
    lead = [_compact(c) for c in cells[:7]]
    return (len(lead) == 7 and lead[0].startswith("POLICY") and lead[1].startswith("INSURED")
            and lead[3].startswith("START") and lead[4].startswith("END")
            and lead[5].startswith("SUMINSURED") and lead[6].startswith("GROSSPREMIUM"))


class PrestigeFairAdapter(_PrestigeAdapter):
    broker = "FAIR"


class PrestigeIbnAdapter(_PrestigeAdapter):
    broker = "IBN"


class PrestigeScibAdapter(_PrestigeAdapter):
    broker = "SCIB"


class PrestigeAfroAsianAdapter(_PrestigeAdapter):
    broker = "AFRO ASIAN"


class PrestigeJbBodaAdapter(_PrestigeAdapter):
    broker = "JB BODA"


class PrestigeJomolaAdapter(_PrestigeAdapter):
    broker = "JOMOLA"


# --- KMD: Oil & Energy quota-share schedules ---------------------------------
_KMD_PREMIUM = (
    ("INSURED", "INSURED NAME"), ("FROM", "PERIOD FROM"), ("TO", "PERIOD TO"), ("UEYEAR", "UW YEAR"),
    ("UWYEAR", "UW YEAR"), ("PRESTIGEONP", "GROSS PREMIUM"),
    ("PRESTIGEDEDUCTIBLE%", "RETENTION %"), ("PRESTIGEDEDUCTIBLE", "RETENTION SUM INSURED"),
    ("PRESTIGENETONP", "RETENTION PREMIUM"),
)
_KMD_CLAIMS = (
    ("INSURED", "INSURED"), ("STARTDATE", "START DATE"), ("ENDDATE", "END DATE"), ("DOL", "LOSS DATE"),
    ("LOSSDETAILS", "LOSS DETAILS"), ("PAIDAMOUNT", "TOTAL CLAIMS PAID"), ("PRESTIGEPPN", "RETENTION %"),
    ("PRESTIGEAMOUNT", "RETENTION AMOUNT"), ("LOCALTTYPPN", "LOCAL TREATY %"),
    ("LOCALTTYAMOUNT", "LOCAL TREATY AMOUNT"), ("FOREIGNTTYPPN", "FOREIGN TREATY %"),
    ("FOREIGNTTYAMOUNT", "FOREIGN TREATY AMOUNT"),
)


def _kmd_table_rule(header, rows, table):
    """KMD 'CLAIMS' tabs: the paid amount and its retention / treaty shares,
    no claim or policy number (too few generic claims signals)."""
    from src.domain.cre_cleaner.core.table_type import PAID
    h = set(header)
    if table.label != PAID and "TOTAL CLAIMS PAID" in h and "RETENTION AMOUNT" in h:
        return PAID, "KMD claims schedule: PAID AMOUNT with Prestige / treaty shares"
    return None


class PrestigeKmdAdapter(ControlledQuarterFiles, SourceLayoutAdapter):
    """KMD schedules: SUM INSURED (100%) | PRESTIGE ONP | MAXIMUM POLICY LIMIT |
    PRESTIGE LINE % | PRESTIGE LINE <CCY> | PRESTIGE DEDUCTIBLE | PRESTIGE
    DEDUCTIBLE (%) | PRESTIGE NET (ONP) | BALANCE SUM INSURED | FOREIGN TREATY
    (% | SI | PREMIUM) | LOCAL TREATY (% | SI | PREMIUM). The band % are shares
    of Prestige's line, so the line is the basis: TOTAL SUM INSURED = PRESTIGE
    LINE, GROSS PREMIUM = PRESTIGE ONP, retention = deductible. Foreign and
    local treaty are summed into TREATY (user decision 2026-10-23) with the
    existing combined-layer record; the 100% sum insured stays in the source."""

    cedant = "PRESTIGE"
    broker = "KMD"
    verified = False
    status_note = "PRESTIGE/KMD: Oil & Energy quota-share schedules (native Excel); line basis; foreign + local treaty summed"
    class_label_extra = {"OIL & ENERGY": "Oil & Gas", "OIL AND ENERGY": "Oil & Gas"}
    settings = {"single_class": "Oil & Gas"}
    premium_alias_extra = {"insured": ["INSURED NAME"], "uw_year": ["UW YEAR"]}
    claims_alias_extra = {
        "insured": ["INSURED"], "total_claims": ["TOTAL CLAIMS PAID"], "details": ["LOSS DETAILS"],
        "ppn_ret": ["RETENTION %"], "amount_ret": ["RETENTION AMOUNT"],
        "ppn_treaty": ["LOCAL TREATY %"], "amount_treaty": ["LOCAL TREATY AMOUNT"],
    }

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        out = [list(r) for r in rows]
        for i, r in enumerate(out[:15]):
            comp = [_compact(c) for c in r]
            if "INSURED" not in comp:
                continue
            if "PRESTIGEONP" in comp and "PRESTIGELINE%" in comp:
                band = out[i - 1] if i else []
                foreign = next((j for j, v in enumerate(band) if "FOREIGN" in _compact(v)), None)
                local = next((j for j, v in enumerate(band) if "LOCAL" in _compact(v)), None)
                for j, c in enumerate(comp):
                    name = next((n for p, n in _KMD_PREMIUM if c == p), None)
                    if c.startswith("PRESTIGELINE") and c != "PRESTIGELINE%":
                        name = "SUM INSURED"
                    elif c == "SUMINSURED" and (foreign is None or j < foreign):
                        name = "ORIGINAL SUM INSURED (100%)"
                    if name:
                        r[j] = name
                for label, start in (("FOREIGN TREATY", foreign), ("LOCAL TREATY", local)):
                    if start is not None and start + 2 < len(r):
                        r[start:start + 3] = [f"{label} %", f"{label} SUM INSURED", f"{label} PREMIUM"]
                exceptions.append(ExceptionRecord(
                    "INFO", "kmd_schedule_header_named", path.name, sheet, i + 1,
                    "KMD schedule: Prestige line = gross basis, deductible = retention, foreign/local treaty bands"))
            elif "PAIDAMOUNT" in comp:
                for j, c in enumerate(comp):
                    name = next((n for p, n in _KMD_CLAIMS if c == p), None)
                    if name:
                        r[j] = name
                if not any("CURRENCY" in _compact(v) for row in out for v in row):
                    exceptions.append(ExceptionRecord(
                        "WARN", "kmd_claims_currency_unstated", path.name, sheet, i + 1,
                        "The claims tab states no currency (the schedule tab's title does); the cleaner's "
                        "default currency applies — confirm before upload"))
            elif "OURSHAREONP" in comp:
                exceptions.append(ExceptionRecord(
                    "WARN", "kmd_layout_not_supported", path.name, sheet, i + 1,
                    "Internal payment memo layout (OUR SHARE ONP | PRESTIGE PPN | PPN GROSS COMM NET …); "
                    "not read — no transaction mapping for it"))
                return []
        return out

    def classify_source_tab(self, file, sheet, rows):
        return retype_tables(file, sheet, rows, self.table_vocab, _kmd_table_rule)

    def check_premium_row(self, prow, *, exceptions, path, sheet, excel_row):
        # TREATY already holds local + foreign (combined), so the layer list is not added again.
        bands = {"RETENTION": (_num(prow.ret_ppn), _num(prow.ret_prem)),
                 "TREATY": (_num(prow.sur_ppn), _num(prow.sur_prem))}
        check_allocation(gross=_num(prow.gross_premium), bands={k: v for k, v in bands.items() if v != (None, None)},
                         exceptions=exceptions, path=path, sheet=sheet, excel_row=excel_row,
                         what="Prestige line premium")

    def _bands(self, header):
        norms = [normalize_header(h) for h in header]

        def col(name):
            return next((i for i, n in enumerate(norms) if n == name), None)
        return col

    def map_premium_columns(self, header, group_row, **kwargs):
        cm = super().map_premium_columns(header, group_row, **kwargs)
        # KMD schedules carry no policy number; the POLICY alias would land on
        # MAXIMUM POLICY LIMIT (a money column). Leave POLICY NO blank.
        policy_i = cm.get("policy_no")
        if policy_i is not None and "LIMIT" in normalize_header(header[policy_i]):
            del cm.mapping["policy_no"]
        col = self._bands(header)
        if col("RETENTION PREMIUM") is None:
            return cm
        cm.ret_ppn, cm.ret_si, cm.ret_prem = col("RETENTION %"), col("RETENTION SUM INSURED"), col("RETENTION PREMIUM")
        cm.sur_ppn, cm.sur_si, cm.sur_prem = col("LOCAL TREATY %"), col("LOCAL TREATY SUM INSURED"), col("LOCAL TREATY PREMIUM")
        cm.sur_label = "LOCAL TREATY"
        cm.fac_ppn = cm.fac_si = cm.fac_prem = None
        cm.extra_treaty_blocks = [("FOREIGN TREATY", col("FOREIGN TREATY %"), col("FOREIGN TREATY SUM INSURED"),
                                   col("FOREIGN TREATY PREMIUM"))]
        cm.combine_treaty_layers = True
        for f, name in (("sum_insured", "SUM INSURED"), ("gross_premium", "GROSS PREMIUM")):
            if col(name) is not None:
                cm.mapping[f] = col(name)
        return cm

    def map_claims_columns(self, header, aliases, sheet_type=""):
        cm = super().map_claims_columns(header, aliases, sheet_type)
        col = self._bands(header)
        for kind, name in (("ret", "RETENTION"), ("treaty", "LOCAL TREATY")):
            if col(f"{name} AMOUNT") is not None:
                cm.mapping["amount_" + kind] = col(f"{name} AMOUNT")
                if col(f"{name} %") is not None:
                    cm.mapping["ppn_" + kind] = col(f"{name} %")
        foreign = col("FOREIGN TREATY AMOUNT")
        if foreign is not None:
            cm.extra_amount_columns = [(foreign, "FOREIGN TREATY")]
            cm.combine_treaty_layers = True
        return cm
