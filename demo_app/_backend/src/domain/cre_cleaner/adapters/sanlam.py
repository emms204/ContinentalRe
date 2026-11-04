"""Sanlam/legacy Allianz source bands and claims; co-insurance is not FAC."""

from src.domain.cre_cleaner.adapters.source_layouts import SourceLayoutAdapter
from src.domain.cre_cleaner.core.normalize import normalize_header
from src.domain.cre_cleaner.core.normalize import parse_number


_CO_SHARE_AMOUNTS = {"SANLAM PROPORTION", "FBNGI PROPORTION", "SANLAMALLIANZ PROPORTION"}
# Only a column named for the treaty. 'PROP %' / 'PROPORTION %' (2025 Q3) is
# the share of whichever cession the row has (treaty or FAC FACILITY), so it is
# not placed in one band (Manual: do not guess allocations).
_CO_SHARE_TREATY_PPN = {"TREATY %"}
# Share headers that some outstanding returns write twice (proportion, then
# amount) -> the PPN alias the first occurrence is read as.
_REPEATED_SHARE_PPN = {
    "ALLIANZ SHARE": "ALLIANZ SHARE PPN",
    "TREATY SHARE": "TREATY SHARE PPN",
    "FAC SHARE": "FAC SHARE PPN",
}


def _is_co_share_layout(norms) -> bool:
    """Claims tabs with Sanlam's co-insurance share (% | Proportion) beside a
    COINSURANCE RECOVERY or NET CLAIM PAID column."""
    return any(h in _CO_SHARE_AMOUNTS for h in norms) and (
        "TOTAL CLAIM PAID" in norms or "COINSURANCE RECOVERY" in norms)


def _claim_numbers(rows):
    """Claim numbers under the first 'CLAIM NO' heading in the top rows."""
    for i, row in enumerate(rows[:10]):
        norms = [normalize_header(v) for v in row]
        col = next((j for j, h in enumerate(norms) if h in {"CLAIM NO", "CLAIM NUMBER", "CLAIMS NO"}), None)
        if col is not None:
            # Claim references have no spaces; group rows ('2019 UNDERWRITING
            # YEAR') and repeated headings in the column are skipped.
            values = (str(r[col] or "").strip().upper() for r in rows[i + 1:] if col < len(r))
            return {v for v in values if v and " " not in v and any(ch.isdigit() for ch in v)}
    return set()


def _drop_repeated_paid_sheets(sheets, notes):
    """2024 Q1: per-class 'SETTLED CLAIM- GEN ACC' / 'SETTLED CLAIMS-FIRE'
    tabs list claims that the JAN / FEB / MARCH settled tabs already hold.
    Manual Step B: one controlling detailed source per class and type, never
    load the same transactions twice. The fuller monthly detail controls; a
    paid tab whose every claim is on other paid tabs is a supporting control."""
    paid = {sn: _claim_numbers(rows) for sn, rows in sheets.items()
            if any(w in sn.upper() for w in ("SETTLED", "PAID")) and "OUTST" not in sn.upper()}
    for sn in sorted(paid, key=lambda k: len(paid[k])):
        claims = paid[sn]
        others = {k: v for k, v in paid.items() if k != sn and v}
        held = set().union(*others.values()) if others else set()
        if claims and claims <= held and len(held) > len(claims):
            on = sorted(k for k, v in others.items() if v & claims)
            sheets[sn] = []
            paid[sn] = set()
            notes.append(("WARN", "claims_sheet_repeats_detail", sn, 0,
                          f"All {len(claims)} claims on this tab are also on {', '.join(on)}; read as a "
                          "supporting control and not loaded (one controlling detailed source; "
                          "compare its totals with the detail tabs)"))
    return sheets, notes


class SanlamArkAdapter(SourceLayoutAdapter):
    cedant = "SANLAM GENERAL INSURANCE"
    broker = "ARK"
    claims_header_scan_all = True
    class_label_extra = {"MAR CAR": "Marine Cargo", "LIABILITY": "General Accident"}
    premium_alias_extra = {
        "class": ["CLASS", "PRODUCT"],
        "sum_insured": ["OUR SHARE SUM INSURED"],
        "gross_premium": ["OUR SHARE GROSS PREMIUM"],
    }
    claims_alias_extra = {
        "class": ["CLASS", "CLASS OF BUSINESS"],
        "insured": ["INSURED CLAIMANT"],
        # Allianz-era loss bordereaux: AZNG SHARE (N) / (%) is the retention,
        # FAC OUT SHARE (N) / (%) the facultative (AZNG % + TREATY % = 100).
        "ppn_ret": ["ALLIANZ SHARE PPN", "FBNGI %", "AZNG SHARE %"],
        "ppn_treaty": ["TREATY SHARE PPN"],
        "ppn_fac": ["FAC SHARE PPN", "FAC OUT SHARE %"],
        "period": ["PERIOD OF INSURED"],
        "period_from": ["POLICY COVER FROM", "PERIOD OF COVER FROM"],
        "period_to": ["POLICY COVER TO", "PERIOD OF COVER TO"],
        "total_claims": [
            "TOTAL CLAIM PAID",
            "TOTAL PAID",
            "TOTAL OST RESERVE",
            "TOTAL CLAIMS OUTSTANDING",
            "NET CLAIM PAID",
        ],
        "amount_ret": [
            "AZNG NET LIABILITY",
            "AZNG SHARE N",
            "ALLIANZ SHARE AMOUNT",
            "ALLIANZ SHARE",
            "FBNG NET",
            "FBGI NET",
            "OIPL NET",
            "FBNGI PROPORTION",
            "SANLAM PROPORTION",
        ],
        "amount_treaty": [
            "TREATY CESSIONS",
            "S TREATY",
            "TREATY 1",
            "TREATY SHARE AMOUNT",
            "TREATY SHARE",
        ],
        "amount_fac": ["FACRE CESSION", "FAC SHARE AMOUNT", "FAC SHARE", "FAC OUT SHARE N"],
        "details": ["DETAILS", "DESCRIPTION OF LOSS"],
    }
    claims_total_fallback = {
        # 2024 Q1 per-class tabs: 'AMOUNT PAID (N)' / 'CLAIM AMOUNT PAID (N)'.
        "paid": ("AMOUNT PAID", "AMOUNT PAID N", "CLAIM AMOUNT PAID N"),
        "outstanding": ("OUTSTANDING RESERVE", "RESERVE"),
    }

    def premium_header_cells(self, raw_rows, header_at, header, group_row):
        # Currency remains in the original rows used by currency detection.
        cleaned = [normalize_header(v) for v in header]
        for i, label in enumerate(cleaned):
            if label in {
                f"{money} {ccy}"
                for money in ("SUM INSURED", "PREMIUM")
                for ccy in ("USD", "NGN", "GBP", "EUR")
            }:
                cleaned[i] = label.rsplit(" ", 1)[0]
        return super().premium_header_cells(raw_rows, header_at, cleaned, group_row)

    def classify_source_tab(self, file, sheet, rows):
        from dataclasses import replace
        from src.domain.cre_cleaner.core.table_type import (
            classify_tab,
            OTHER,
            OUTSTANDING,
            UNKNOWN,
        )

        typed = classify_tab(file, sheet, rows, vocab=self.table_vocab)
        tables = []
        for table in typed.tables:
            if table.header_row < 0:
                tables.append(table)
                continue
            header = [normalize_header(v) for v in rows[table.header_row]]
            status = next(
                (i for i, h in enumerate(header) if h in {"REMARK", "STATUS"}), None
            )
            reserve = next(
                (
                    i
                    for i, h in enumerate(header)
                    if h
                    in {
                        "RESERVE",
                        "TOTAL CLAIMS OUTSTANDING",
                        "GROSS ESTIMATED CLAIM",
                        "GROSS ESTIMATED CLAIM RESERVE",
                    }
                ),
                None,
            )
            policy = next(
                (
                    i
                    for i, h in enumerate(header)
                    if h in {"POLICY NO", "POLICY NUMBER"}
                ),
                None,
            )
            if status is None or reserve is None or policy is None:
                tables.append(table)
                continue
            data = [
                r
                for r in rows[table.header_row + 1 : table.end_row]
                if policy < len(r) and str(r[policy] or "").strip()
            ]
            states = {
                normalize_header(r[status]) if status < len(r) else "" for r in data
            }
            paid_cols = [
                i
                for i, h in enumerate(header)
                if h
                in {
                    "AMOUNT PAID",
                    "TOTAL CLAIM PAID",
                    "TOTAL CLAIMS PAID",
                    "NET CLAIM PAID",
                }
            ]
            paid_money = any(
                parse_number(r[i]) not in (None, 0)
                for r in data
                for i in paid_cols
                if i < len(r)
            )
            if data and states == {"OUTSTANDING"} and not paid_money:
                table = replace(
                    table,
                    label=OUTSTANDING,
                    confidence=1.0,
                    evidence=table.evidence
                    + [
                        "Explicit outstanding status on every policy row; paid amounts absent"
                    ],
                    conflicts=[],
                )
            elif "OUTSTANDING" in states and (paid_money or len(states) > 1):
                table = replace(
                    table,
                    label=UNKNOWN,
                    confidence=0.0,
                    conflicts=table.conflicts
                    + [
                        "Outstanding status conflicts with paid money or mixed row statuses; review required"
                    ],
                )
            tables.append(table)
        # As classify_tab: a header block with no transactions (OTHER) does not
        # decide the tab's type; blocks of different types still do (UNKNOWN).
        txn = [t for t in tables if t.label != OTHER]
        labels = {t.label for t in txn}
        if tables:
            typed = replace(
                typed,
                tables=tables,
                label=(OTHER if not txn
                       else next(iter(labels)) if len(labels) == 1 else UNKNOWN),
                confidence=min(t.confidence for t in (txn or tables)),
                conflicts=[c for t in tables for c in t.conflicts],
            )
        return typed

    def map_claims_columns(self, header, aliases, sheet_type=""):
        cm = super().map_claims_columns(header, aliases, sheet_type)
        norms = [normalize_header(v) for v in header]
        # These source columns contain money despite the word Proportion.
        for label in ("SANLAM PROPORTION", "FBNGI PROPORTION"):
            if label in norms:
                cm.mapping["amount_ret"] = norms.index(label)
                break
        if sheet_type == "outstanding":
            for label in (
                "TOTAL CLAIMS OUTSTANDING",
                "TOTAL OST RESERVE",
                "RESERVE",
                "GROSS ESTIMATED CLAIM RESERVE",
                "GROSS ESTIMATED CLAIM",
            ):
                if label in norms:
                    cm.mapping["total_claims"] = norms.index(label)
                    break
        cm.combine_treaty_layers = True
        cm.extra_amount_columns = [
            (i, h)
            for i, h in enumerate(norms)
            if h in {"TREATY 1", "TREATY 2", "TREATY SURPLUS 1", "TREATY SURPLUS 2"}
            and i != cm.get("amount_treaty")
        ]
        if _is_co_share_layout(norms):
            # 'TOTAL CLAIM PAID | SANLAM % | SANLAM PROPORTION | COINSURANCE
            # RECOVERY | (TREATY %) | TREATY 1 | TREATY 2 | FAC FACILITY |
            # LOCAL FAC | SALVAGE | NET CLAIM PAID': the % / Proportion pair is
            # Sanlam's co-insurance share of the claim, not the retention. Net
            # Claim Paid is what Sanlam keeps; the source states no retention %.
            # Without that column (2025 Q4) the retention is left blank.
            cm.mapping.pop("ppn_ret", None)
            if "NET CLAIM PAID" in norms:
                net = norms.index("NET CLAIM PAID")
                cm.mapping["amount_ret"] = net
                if "TOTAL CLAIM PAID" in norms and cm.get("total_claims") == net:
                    cm.mapping["total_claims"] = norms.index("TOTAL CLAIM PAID")
            else:
                cm.mapping.pop("amount_ret", None)
            share = next((i for i, h in enumerate(norms) if h in _CO_SHARE_TREATY_PPN), None)
            if share is not None:
                cm.mapping["ppn_treaty"] = share
            else:
                cm.mapping.pop("ppn_treaty", None)
            if "FAC FACILITY" in norms:
                cm.mapping["amount_fac"] = norms.index("FAC FACILITY")
            cm.mapping.pop("ppn_fac", None)
        return cm

    def claims_header_cells(self, header, band_row, data_rows=(), notes=None):
        cells = super().claims_header_cells(header, band_row, data_rows=data_rows, notes=notes)
        norms = [normalize_header(v) for v in cells]
        # 2023 OCR: ALLIANZ / TREATY / FAC SHARE are written twice, the share
        # proportions first and the amounts after. A first column of
        # proportions is the band PPN; the amount alias then takes the second.
        for label, ppn in _REPEATED_SHARE_PPN.items():
            cols = [i for i, h in enumerate(norms) if h == label]
            if len(cols) != 2:
                continue
            values = [parse_number(r[cols[0]]) for r in data_rows if cols[0] < len(r)]
            values = [v for v in values if v is not None]
            if values and any(values) and (all(0 <= v <= 1 for v in values)
                                           or all(0 <= v <= 100 for v in values)):
                cells[cols[0]] = ppn
                norms[cols[0]] = ppn
                if notes is not None:
                    notes.append(("INFO", "repeated_share_header",
                                  f"{label!r} appears twice: column {cols[0] + 1} (proportions) read as "
                                  f"{ppn!r}, column {cols[1] + 1} as the amount"))
        if _is_co_share_layout(norms) and "LOCAL FAC" in norms and notes is not None:
            i = norms.index("LOCAL FAC")
            values = [parse_number(r[i]) for r in data_rows if i < len(r)]
            values = [v for v in values if v]
            if values:
                notes.append(("WARN", "local_fac_left_out",
                              f"LOCAL FAC kept out of the RET / TREATY / FAC bands (FAC is FAC FACILITY): "
                              f"{len(values)} rows, {sum(values):,.2f} in total"))
        return cells

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        # Reserve-only exports omit claim numbers and loss dates. Their
        # explicit reserve heading still distinguishes them from premium.
        from src.domain.cre_cleaner.models import ExceptionRecord

        result = list(rows)
        for i, row in enumerate(rows):
            norms = [normalize_header(v) for v in row]
            if {"OUR SHARE SUM INSURED", "OUR SHARE GROSS PREMIUM"} <= set(norms):
                from itertools import permutations
                import math

                si_col = norms.index("OUR SHARE SUM INSURED")
                gp_col = norms.index("OUR SHARE GROSS PREMIUM")
                for j, label in enumerate(norms):
                    if label not in {"RETENTION", "TREATY"} or norms[j + 1 : j + 3] != [
                        "",
                        "",
                    ]:
                        continue
                    samples = []
                    for data in rows[i + 1 : i + 31]:
                        if len(data) <= max(j + 2, si_col, gp_col):
                            continue
                        values = [
                            parse_number(data[c])
                            for c in (si_col, gp_col, j, j + 1, j + 2)
                        ]
                        if (
                            all(v is not None for v in values)
                            and values[0]
                            and values[1]
                        ):
                            samples.append(values)
                    candidates = []
                    for order in permutations(range(3)):
                        if samples and all(
                            any(
                                math.isclose(
                                    v[2 + order[0]] / v[0],
                                    v[2 + order[2]] / scale,
                                    rel_tol=1e-5,
                                    abs_tol=1e-8,
                                )
                                and math.isclose(
                                    v[2 + order[1]] / v[1],
                                    v[2 + order[2]] / scale,
                                    rel_tol=1e-5,
                                    abs_tol=1e-8,
                                )
                                for scale in (1, 100)
                            )
                            for v in samples
                        ):
                            candidates.append(order)
                    if len(candidates) == 1:
                        result[i] = list(result[i])
                        for offset, suffix in zip(
                            candidates[0], ("SUM INSURED", "PREMIUM", "PPN")
                        ):
                            result[i][j + offset] = label + " " + suffix
                        exceptions.append(
                            ExceptionRecord(
                                "INFO",
                                "allocation_subheaders_verified",
                                path.name,
                                sheet,
                                i + 1,
                                f"{label} unlabeled columns identified by matching both source SI and premium proportions on {len(samples)} rows; values unchanged",
                            )
                        )
            if "CLAIM NO" in norms and "INSURED" in norms and "RESERVE" in norms:
                # Older FBN/Oasis reports print labels over two rows. Only
                # complete observed label pairs are joined; money stays put.
                below = rows[i + 1] if i + 1 < len(rows) else []
                result[i] = list(row)
                for j, h in enumerate(norms):
                    lower = normalize_header(below[j]) if j < len(below) else ""
                    if (h, lower) in {("DATE OF", "LOSS"), ("DETAILS OF", "INCIDENT")}:
                        result[i][j] = h + " " + lower
                    elif h == "RESERVE":
                        result[i][j] = "TOTAL CLAIMS OUTSTANDING"
                exceptions.append(
                    ExceptionRecord(
                        "INFO",
                        "legacy_claim_header_normalized",
                        path.name,
                        sheet,
                        i + 1,
                        "Split legacy claim labels joined; source row and amount columns preserved",
                    )
                )
            if (
                "GROSS ESTIMATED CLAIM RESERVE" in norms
                and any("POLICY" in h for h in norms)
                and any("INSURED" in h for h in norms)
            ):
                # If paid money also exists, keep the conflicting type visible
                # for human review instead of reclassifying a mixed extract.
                if any(
                    h in {"AMOUNT PAID", "TOTAL CLAIM PAID", "TOTAL CLAIMS PAID"}
                    for h in norms
                ):
                    continue
                result[i] = list(row)
                result[i][
                    norms.index("GROSS ESTIMATED CLAIM RESERVE")
                ] = "TOTAL CLAIMS OUTSTANDING"
                exceptions.append(
                    ExceptionRecord(
                        "INFO",
                        "reserve_header_normalized",
                        path.name,
                        sheet,
                        i + 1,
                        "Gross Estimated Claim/Reserve mapped as an outstanding total; source figures and locations preserved",
                    )
                )
        return result

    def claims_exclude(self, field, norm):
        return (
            super().claims_exclude(field, norm)
            or norm.startswith("IF FAC")
            or field == "amount_treaty"
            and norm == "TOTAL RECOVERY"
            or field in {"amount_ret", "amount_treaty", "amount_fac"}
            and norm.startswith("CO SHARE")
            # 'Claim Process Period (Days)' is settlement time, not the cover
            # period (Manual: keep Insurance Period From and To).
            or field == "period"
            and "PROCESS" in norm
        )

    def read_source_workbook(self, path):
        sheets, notes = super().read_source_workbook(path)
        return _drop_repeated_paid_sheets(sheets, notes)


class SanlamScibAdapter(SanlamArkAdapter):
    broker = "SCIB"


class SanlamJordansAdapter(SanlamArkAdapter):
    broker = "JORDANS"
    # 2020–2022 premium tabs: 'FIRE SURPLUS 1' / 'MARINE CARGO' / 'ENGINEERING'
    # header rows group the policies; the Product column ('9002 - IAR
    # Comprehensive Policy') is the product, not the treaty class.
    premium_section_class_first = True

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        # 2021: the first block's class sits on the band row under the header
        # ('FIRE SURPLUS 1 | SUM INSURED | PREMIUM | %'), which is read as
        # band labels. Remember it for each block's rows (up to its total row).
        from src.domain.cre_cleaner.core.class_labels import banner_class_label

        blocks = self.__dict__.setdefault("_band_row_classes", {})
        found = []
        for i in range(len(rows) - 1):
            if normalize_header(rows[i][0] if rows[i] else "") != "PRODUCT":
                continue
            band = rows[i + 1]
            label = banner_class_label(str(band[0] or "")) if band and band[0] else ""
            if not label or not any(normalize_header(v) in {"SUM INSURED", "PREMIUM"} for v in band[1:]):
                continue
            end = next((k for k in range(i + 2, len(rows))
                        if rows[k] and not any(str(v or "").strip() for v in rows[k][:7])
                        and any(parse_number(v) for v in rows[k][7:])), len(rows))
            found.append((label, str(band[0]).strip(), i + 3, end + 1))
        blocks[(path.name, sheet)] = found
        self._index_numbered_claim_sections(sheet, rows, path=path, exceptions=exceptions)
        return super().prepare_source_sheet(sheet, rows, path=path, exceptions=exceptions)

    def _index_numbered_claim_sections(self, sheet, rows, *, path, exceptions):
        """Verify source numbering before using a section as a row's class.

        Some claims exports label the numbering column CLASS. A section's numbers
        are numbering when they run 1..N beneath an explicit approved section.
        Outstanding-by-year tabs (2022–2024 'OS 2021', '2021') restart the
        numbers in each section with gaps where rows were removed: those count
        when the header block has two or more approved sections, every number
        increases within its section and the column holds nothing but approved
        headings and whole numbers. Text classes, repeats, a lone section with
        gaps and unknown sections retain normal review.
        """
        from src.domain.cre_cleaner.core.class_labels import banner_class_label, approved_class_labels
        from src.domain.cre_cleaner.models import ExceptionRecord

        indexed = {}
        self.__dict__.setdefault('_numbered_claim_sections', {})[(str(path.resolve()), sheet)] = indexed
        columns = None
        section = None
        pending = []
        block = []          # (section, pending) of the current header block
        block_ok = True     # no policy row outside an approved section

        def mark(sec, rows_seq):
            for source_row, sequence in rows_seq:
                indexed[source_row] = dict(rule='numbered_class_section',
                    section_class=sec[0], section_label=sec[1],
                    section_row=sec[2], source_sequence=sequence)

        def finish():
            if not section or not pending:
                return
            block.append((section, list(pending)))
            if all(sequence == n for n, (_, sequence) in enumerate(pending, 1)):
                mark(section, pending)
                exceptions.append(ExceptionRecord('INFO', 'numbered_class_section',
                    path.name, sheet, section[2],
                    f'{len(pending)} source sequence numbers beneath {section[1]!r} '
                    'verified as 1..N; section supplies class, source cells unchanged.'))

        def finish_block():
            runs = [[s for _, s in p] for _, p in block]
            if (not block_ok or len(block) < 2
                    or any(s is None for run in runs for s in run)
                    or any(b <= a for run in runs for a, b in zip(run, run[1:]))
                    or all(r in indexed for _, p in block for r, _ in p)):
                return
            for sec, rows_seq in block:
                mark(sec, rows_seq)
            exceptions.append(ExceptionRecord('INFO', 'numbered_class_section',
                path.name, sheet, block[0][0][2],
                f'{sum(map(len, runs))} source sequence numbers increase within each of '
                f'{len(block)} approved sections (numbering restarts per section, with gaps); '
                'each section supplies its rows\' class, source cells unchanged.'))

        allowed = approved_class_labels(self.class_map()) - {'Other', 'Facultative'}
        for i, row in enumerate(rows, 1):
            norms = [normalize_header(v) for v in row]
            policy = next((j for j, h in enumerate(norms) if h in {'POLICY NO', 'POLICY NUMBER'}), None)
            claim = next((j for j, h in enumerate(norms) if h in {'CLAIM NO', 'CLAIM NUMBER', 'CLAIMS NO'}), None)
            if 'CLASS' in norms and policy is not None and claim is not None:
                finish()
                finish_block()
                columns = (norms.index('CLASS'), policy, claim)
                section, pending, block, block_ok = None, [], [], True
                continue
            if columns is None:
                continue
            ci, pi, ni = columns
            filled = [(j, v) for j, v in enumerate(row) if v is not None and str(v).strip()]
            if len(filled) == 1 and filled[0][0] == ci:
                finish()
                raw = str(filled[0][1]).strip()
                label = banner_class_label(raw)
                section = (label, raw, i) if label in allowed else None
                if section is None and parse_number(raw) is None:
                    block_ok = False  # an unknown section heading
                pending = []
                continue
            if pi < len(row) and ni < len(row) and row[pi] and row[ni]:
                if not section:
                    block_ok = False  # a policy row outside an approved section
                    continue
                raw = row[ci] if ci < len(row) else None
                number = parse_number(raw)
                sequence = int(number) if number is not None and number > 0 and float(number).is_integer() else None
                pending.append((i, sequence))
        finish()
        finish_block()

    def check_claims_row(self, claim, *, row, cmap, exceptions, path, sheet, excel_row):
        evidence = self.__dict__.get('_numbered_claim_sections', {}).get(
            (str(path.resolve()), sheet), {}).get(excel_row)
        if evidence:
            claim.class_name = evidence['section_class']
            claim.audit.class_hint = evidence['section_class']
            claim.audit.class_source = 'banner'
            claim.audit.class_label_raw = evidence['section_label']
            claim.audit.class_resolution = dict(evidence)

    def resolve_premium_class(self, *, sheet_hint, section, raw, **context):
        from src.domain.cre_cleaner.core.class_labels import is_unresolved_class, normalize_class_label

        row = context.get("excel_row") or 0
        block = next((b for b in self.__dict__.get("_band_row_classes", {}).get(
            (getattr(context.get("path"), "name", ""), context.get("sheet")), []) if b[2] <= row < b[3]), None)
        if not section and block:
            return block[0], "banner", block[1]
        cm = self.class_map()
        for value, source in ((section, "banner"), (sheet_hint, "sheet")):
            if value and not is_unresolved_class(value) and normalize_class_label(value, cm) not in {"", "Other"}:
                return value, source, value if source == "banner" else context.get("sheet", value)
        mapped = normalize_class_label(raw, cm)
        if mapped not in {"", "Other"}:
            return mapped, "column", raw
        return raw or section or sheet_hint, "column" if raw else "banner", raw or section or sheet_hint


class SanlamAgricAdapter(SanlamArkAdapter):
    broker = "AGRIC"
    settings = {"single_class": "Agriculture"}
