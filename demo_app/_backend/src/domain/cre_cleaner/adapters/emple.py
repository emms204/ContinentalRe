"""EMPLE (formerly Old Mutual General) quarterly bordereaux via SCIB and AON.

Both broker folders hold the same cedant-produced workbooks (many quarters
are cell-identical across the two), so the layout is keyed on era, not
broker. Premium tabs are one class per tab with a group-label row
(<--GROSS VALUE-->, <--RETENTION-->, <--FIRST SURPLUS-->, <--FACULTATIVE
VALUE-->) over repeated 'SUM INSURED | % | PREMIUM' blocks. Paid and
outstanding sheets carry the class in 'NAICOM Class Description'.
"""
from __future__ import annotations

import re
from datetime import datetime

from src.domain.cre_cleaner.adapters.source_layouts import SourceLayoutAdapter
from src.domain.cre_cleaner.adapters.workbook_rules import ControlledQuarterFiles, retype_tables, terrorism_tab
from src.domain.cre_cleaner.core.class_labels import is_unresolved_class, normalize_class_label
from src.domain.cre_cleaner.core.map_columns import is_share_header
from src.domain.cre_cleaner.core.normalize import clean_text, normalize_header, parse_number
from src.domain.cre_cleaner.models import ExceptionRecord

_YMD_DOT = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})$")
_UW_SECTION = re.compile(r'(19\d{2}|20\d{2})\s+U\s*/\s*YR', re.I)
_ARROWS = re.compile(r"^[<>\-\s]+$|^<-")
_PAIR_PPN = {"NET", "TTY", "FAC"}
_CLAIM_NO = {"CLAIM NO", "CLAIM NO."}
_GROSS_LOSS = {"GROSS LOSS", "AMOUNT MANUAL", "TOTAL"}
_RECOVERY = {"TREATY RECOVERY", "TREATY AMOUNT", "RETENTION AMOUNT", "RETENTION ANOUNT"}


def _cell(v) -> str:
    return clean_text(v) if v is not None else ""


def _num(v):
    x = parse_number(v)
    return x if isinstance(x, (int, float)) else None


def ymd_dot_dates(sheet, rows, path, exceptions):
    """'2017.03.12' (year first, dots) → a date. Only an exact YYYY.MM.DD
    string that is a valid calendar date is converted: the same date in
    another notation, never a day/month reinterpretation."""
    n = 0
    out = []
    for r in rows:
        nr = list(r)
        for j, v in enumerate(nr):
            if type(v) is str:
                m = _YMD_DOT.match(v.strip())
                if m:
                    try:
                        nr[j] = datetime(int(m[1]), int(m[2]), int(m[3]))
                        n += 1
                    except ValueError:
                        pass
        out.append(nr)
    if n:
        exceptions.append(ExceptionRecord(
            "INFO", "ymd_dot_dates_read", path.name, sheet, 0,
            f"{n} date cell(s) written YYYY.MM.DD read as dates (notation only; values unchanged)"))
    return out


def name_pair_amount_headers(sheet, rows, path, exceptions):
    """Outstanding 2023 H1: 'NET | NET | TTY | TTY | FAC | FAC', or the amount
    cell left blank after its proportion. The second column of each pair is
    the amount (proportion ≤ 1, amount = proportion × reserve); name it
    '<X> AMOUNT' so the pair maps like the later 'NET | NET AMOUNT'."""
    out = [list(r) for r in rows]
    for ri, r in enumerate(out):
        norms = [normalize_header(v) for v in r]
        if "CLAIM NO" not in norms or not (set(norms) & _PAIR_PPN):
            continue
        named = []
        for i in range(1, len(r)):
            prev = norms[i - 1]
            if prev in _PAIR_PPN and norms[i] in {prev, ""}:
                r[i] = prev + " AMOUNT"
                norms[i] = prev + " AMOUNT"
                named.append(prev)
        if named:
            exceptions.append(ExceptionRecord(
                "INFO", "pair_amount_header_named", path.name, sheet, ri + 1,
                "Amount column of each proportion/amount pair had a repeated or blank header: "
                + ", ".join(f"{p} AMOUNT" for p in named)))
    return out


def triplets(header):
    """Start columns of the repeated 'SUM INSURED | % | PREMIUM' blocks."""
    n = [normalize_header(v) for v in header]
    return [i for i in range(len(n) - 2)
            if n[i] == "SUM INSURED" and is_share_header(n[i + 1]) and n[i + 2] == "PREMIUM"]


def join_split_premium_header(sheet, rows, path, exceptions):
    """2022 Q2: S/N … END_DATE sit on the band-label row and the money fields
    on the row below. Copy the field names down into the empty cells of the
    money row so one row holds the whole header; the band labels stay above."""
    out = None
    for h in range(1, min(len(rows), 15)):
        below, above = rows[h], rows[h - 1]
        if not triplets(below) or "POLICY NO" in [normalize_header(v) for v in below]:
            continue
        if "POLICY NO" not in [normalize_header(v) for v in above]:
            continue
        out = out or [list(r) for r in rows]
        row = out[h] + [None] * max(0, len(above) - len(out[h]))
        moved = []
        for c, v in enumerate(above):
            if _cell(v) and not _cell(row[c]) and not _ARROWS.match(_cell(v)):
                row[c] = v
                moved.append(_cell(v))
        out[h] = row
        exceptions.append(ExceptionRecord(
            "INFO", "split_header_joined", path.name, sheet, h + 1,
            f"Field names on row {h} joined to the allocation header on row {h + 1}: {', '.join(moved)}"))
    return out or rows


def cut_side_table(sheet, rows, path, exceptions):
    """2021–2022 paid sheets keep a reconciliation table to the right of the
    bordereau (Client Name, Claim No, … OCR RETENTION, WHT, TOTAL). Columns
    from the blank gap before its second 'Claim No' are not paid claims."""
    for h, r in enumerate(rows[:15]):
        norms = [normalize_header(v) for v in r]
        hits = [i for i, n in enumerate(norms) if n in _CLAIM_NO]
        if len(hits) < 2:
            continue
        gap = next((i for i in range(hits[0] + 1, hits[1]) if not norms[i]), None)
        if gap is None:
            continue
        exceptions.append(ExceptionRecord(
            "INFO", "side_table_excluded", path.name, sheet, h + 1,
            f"Second table from column {gap + 1} ({', '.join(n for n in norms[gap:] if n)[:160]}) "
            "is a reconciliation beside the paid bordereau; not read as claims"))
        return [list(x[:gap]) for x in rows]
    return rows


def claims_uw_sections(sheet, rows, path, exceptions, aliases):
    """Read standalone YYYY U/YR banners inside a claims table only.

    Add a parser-only column without moving source cells or rows. Explicit
    row-level UW columns take precedence; headers and nonempty non-record
    rows close the section. Blank spacers do not close it.
    """
    def section_year(row):
        cells = [_cell(v) for v in row if _cell(v)]
        return _UW_SECTION.fullmatch(cells[0]) if len(cells) == 1 else None

    if not any(section_year(r) for r in rows):
        return rows
    width = max((len(r) for r in rows), default=0)
    out = [list(r) + [None] * (width + 1 - len(r)) for r in rows]
    claim = policy = None
    year = marker = None
    enabled = False
    uw_headers = {normalize_header(v) for v in aliases['uw_yr']}
    for i, original in enumerate(rows):
        norms = [normalize_header(v) for v in original]
        if set(norms) & _CLAIM_NO and set(norms) & {'POLICY', 'POLICY NO'}:
            claim = next(c for c, n in enumerate(norms) if n in _CLAIM_NO)
            policy = next(c for c, n in enumerate(norms) if n in {'POLICY', 'POLICY NO'})
            year = marker = None
            enabled = not bool(set(norms) & uw_headers)
            if enabled:
                out[i][width] = 'UW YR'
            continue
        if claim is None or not enabled:
            continue
        cells = [_cell(v) for v in original if _cell(v)]
        match = section_year(original)
        if match:
            year, marker = int(match[1]), i + 1
            continue
        has_ids = all(c < len(original) and _cell(original[c]) for c in (claim, policy))
        if has_ids and year is not None:
            out[i][width] = year
            exceptions.append(ExceptionRecord(
                'INFO', 'claims_uw_section_read', path.name, sheet, i + 1,
                f'UW year {year} from explicit U/YR section at source row {marker}'))
        elif cells and not has_ids:
            year = marker = None
    return out


def name_loss_date_column(sheet, rows, path, exceptions):
    """2023 Q1 heads the loss-date column 'LOSS' (its cells are dates). Name
    it 'LOSS DATE' when the cells under it are dates, so the claims header is
    found and the column is never read as an amount."""
    from src.domain.cre_cleaner.core.normalize import parse_date
    out = None
    for h, r in enumerate(rows):
        norms = [normalize_header(v) for v in r]
        if "CLAIM NO" not in norms or "LOSS" not in norms:
            continue
        c = norms.index("LOSS")
        below = [x[c] for x in rows[h + 1:h + 30] if c < len(x) and _cell(x[c])]
        if below and sum(parse_date(v) is not None for v in below) * 2 > len(below):
            out = out or [list(x) for x in rows]
            out[h][c] = "LOSS DATE"
            exceptions.append(ExceptionRecord(
                "INFO", "loss_date_header_named", path.name, sheet, h + 1,
                f"Column {c + 1} headed 'LOSS' holds dates; read as LOSS DATE"))
    return out or rows


def skip_unlabelled_claims_subtotals(sheet, rows, path, exceptions):
    """Claims class subtotals carry money with no claim and no policy number
    ('ENGINEERING INSURANCE' in the insured column), or a word in the numeric
    claim-number column ('COUNT | 1920 | <reserve total>'). Manual
    Part Three: subtotals are not transactions. Blanked and listed."""
    out, skipped = None, []
    claim_c = policy_c = None
    for i, r in enumerate(rows):
        norms = [normalize_header(v) for v in r]
        if set(norms) & _CLAIM_NO and any(n in {"POLICY", "POLICY NO", "POLICY NO."} for n in norms):
            claim_c = next(j for j, n in enumerate(norms) if n in _CLAIM_NO)
            policy_c = next(j for j, n in enumerate(norms) if n in {"POLICY", "POLICY NO", "POLICY NO."})
            continue
        if claim_c is None:
            continue
        ids = [_cell(r[c]) if c < len(r) else "" for c in (claim_c, policy_c)]
        money = [v for v in r if _num(v) not in (None, 0) and not isinstance(v, datetime)]
        # EMPLE claim numbers are numeric; a word there ('COUNT', 'TOTAL') labels a total row.
        label_in_claim_no = bool(ids[0]) and ids[0].isalpha()
        if (label_in_claim_no or not any(ids)) and money and any(_cell(v) for v in r if isinstance(v, str)):
            out = out or [list(x) for x in rows]
            out[i] = [None] * len(r)
            skipped.append(f"r{i + 1} {' '.join(_cell(v) for v in r if isinstance(v, str))[:40]}")
    if skipped:
        exceptions.append(ExceptionRecord(
            "INFO", "claims_subtotal_row_skipped", path.name, sheet, 0,
            f"{len(skipped)} labelled subtotal row(s) without claim or policy number not read: "
            + "; ".join(skipped[:12])))
    return out or rows


def _marker_row(rows, word):
    for i, r in enumerate(rows):
        cells = [normalize_header(v) for v in r if _cell(v)]
        if word in cells and len(cells) <= 2:
            return i
    return None


def _block(rows, start, end):
    """(policy column, triplet starts, data row indices) of the first premium
    table between start and end."""
    for h in range(start, end):
        norms = [normalize_header(v) for v in rows[h]]
        if "POLICY NO" in norms:
            pol = norms.index("POLICY NO")
            data = [i for i in range(h + 1, end) if pol < len(rows[i]) and _cell(rows[i][pol])]
            return h, pol, triplets(rows[h]), data
    return None


def merge_quota_share_surplus_views(sheet, rows, path, exceptions):
    """2025 terrorism tabs list the same risks twice: a QUOTA SHARE block
    (retention | quota-share treaty | surplus — its third band still headed
    FACULTATIVE VALUE) and a SURPLUS block (quota-share layer | surplus).
    Manual Part Four step 9: one row per risk with separate Quota Share and
    surplus layers. The SURPLUS view is dropped only when every one of its
    rows is a QUOTA SHARE risk with the same surplus premium; otherwise
    nothing changes and the tab is flagged for review."""
    qs, sur = _marker_row(rows, "QUOTA SHARE"), _marker_row(rows, "SURPLUS")
    if qs is None or sur is None or sur <= qs:
        return rows
    a, b = _block(rows, qs, sur), _block(rows, sur, len(rows))
    if not a or not b or len(a[2]) < 3 or len(b[2]) < 2:
        return rows
    (ah, apol, at, adata), (_bh, bpol, bt, bdata) = a, b
    by_policy = {}
    for i in adata:
        by_policy.setdefault(_cell(rows[i][apol]), []).append(i)
    unmatched = []
    for j in bdata:
        sur_prem = _num(rows[j][bt[1] + 2]) if bt[1] + 2 < len(rows[j]) else None
        cands = [_num(rows[i][at[2] + 2]) for i in by_policy.get(_cell(rows[j][bpol]), [])]
        if sur_prem is None or not any(c is not None and abs(c - sur_prem) <= 0.01 for c in cands):
            unmatched.append(_cell(rows[j][bpol]))
    if unmatched:
        exceptions.append(ExceptionRecord(
            "WARN", "quota_share_surplus_views_unmatched", path.name, sheet, sur + 1,
            "QUOTA SHARE and SURPLUS blocks could not be matched risk by risk "
            f"({', '.join(unmatched)}); both left as in the source — review so risks are not counted twice"))
        return rows
    band = next((k for k in range(ah - 1, qs, -1)
                 if any("SURPLUS" in normalize_header(v) for v in rows[k])), None)
    if band is None:
        return rows
    out = [list(r) for r in rows]
    for c, v in enumerate(out[band]):
        u = normalize_header(v)
        if "FIRST SURPLUS" in u:
            out[band][c] = "QUOTA SHARE"
        elif "FACULTATIVE" in u:
            out[band][c] = "FIRST SURPLUS"
    for k in range(sur, len(out)):
        out[k] = [None] * len(out[k])
    exceptions.append(ExceptionRecord(
        "INFO", "quota_share_surplus_views_merged", path.name, sheet, sur + 1,
        f"SURPLUS block ({len(bdata)} row(s)) restates risks of the QUOTA SHARE block with the same "
        "surplus premium; read once from the QUOTA SHARE block as retention / quota share / first surplus"))
    return out


def _unplaced(label, cm) -> bool:
    """Too broad (bare MARINE) or not a class at all ('Marine Insurance')."""
    return is_unresolved_class(label) or normalize_class_label(label, cm) in {"", "Other"}


def _emple_table_rule(header, rows, table):
    """Paid sheets ('LOSS OFFSET', 'LOSS NET OFF', 'PAID LOSS BORDEREAU')
    carry a gross loss and its recoveries and never a reserve column."""
    from src.domain.cre_cleaner.core.table_type import PAID, UNKNOWN
    h = set(header)
    if table.label == UNKNOWN and h & _GROSS_LOSS and h & _RECOVERY and not any("RESERVE" in x for x in h):
        return PAID, "gross loss with retention/treaty recoveries and no reserve column"
    return None


class _EmpleAdapter(ControlledQuarterFiles, SourceLayoutAdapter):
    verified = False
    # AON 2023 'PREMIUM BORDEREAU' workbooks carry the paid-loss tab too.
    premium_files_may_hold_claims = True
    status_note = ("EMPLE: source-layout mapping, not checked against approved gold workbooks; "
                   "the SCIB and AON folders hold overlapping copies of the same cedant workbooks — "
                   "do not load both partners for one quarter without review")
    claims_header_scan_all = True
    class_label_extra = {
        "FIRE TERRORISM": "Terrorism & PVT",
        "GPA": "General Accident",
        "GROUP PERSONAL ACCIDENT": "General Accident",
        "PERSONAL ACCIDENT": "General Accident",
        # NAICOM 'Energy Exploration/Development' (claims/outstanding class
        # column): Manual mapping 'Oil and Gas → OIL AND GAS'; store map
        # oil_and_energy → Oil & Gas.
        "ENERGY EXPLORATION/DEVELOPMENT": "Oil & Gas",
        "ENERGY": "Oil & Gas",
        # Fire & Special Perils; EMPLE's own fire policies are numbered emP/FSP/….
        "FSP": "Fire",
    }
    class_label_exact_only = frozenset({"ENERGY", "FSP"})
    premium_alias_extra = {
        "insured": ["CLIENT NAME"],
        "mpl": ["MPL%", "MPL 100%"],
        "gross_premium": ["TOT PREMIUM"],
    }
    claims_alias_extra = {
        "insured": ["CLIENT NAME", "CLIENT"],
        "class": ["NAICOM CLASS DESCRIPTION"],
        "policy_no": ["POLICY NO", "POLICY"],
        "date_of_loss": ["DATE OF LOSS", "LOSS DATE"],
        "total_claims": ["GROSS LOSS", "AMOUNT MANUAL", "RESERVE", "TOTAL"],
        "ppn_ret": ["RETENTION %", "RETENTION%", "RET %", "RET. %", "NET"],
        "amount_ret": ["RETENTION AMOUNT", "RETENTION ANOUNT", "RET AMT", "RET. AMT.", "RET AMT.", "NET AMOUNT"],
        "ppn_treaty": ["TREATY %", "TREATY%", "TTY"],
        "amount_treaty": ["TREATY RECOVERY", "TREATY AMOUNT", "TREATY AMT", "TREATY AMT.", "TTY AMOUNT"],
        "ppn_fac": ["FACULTATIVE %", "FAC %", "FAC"],
        "amount_fac": ["FAC RECOVERY", "FAC AMOUNT", "FAC AMT", "FAC AMT."],
        "details": ["DESCRIPTION OF LOSS", "DESCRIPTION"],
    }

    def premium_header_row(self, raw_rows, found):
        # 2019: a partial identity/gross heading precedes the band row and
        # complete allocation heading. Never skip data to reach a later table.
        first = [normalize_header(v) for v in raw_rows[found]]
        identity = {'POLICY NO', 'CLIENT NAME', 'EFFECTIVE DATE', 'END DATE'}
        if triplets(raw_rows[found]) or not identity <= set(first):
            return found
        for h in range(found + 1, min(found + 4, len(raw_rows))):
            candidate = [normalize_header(v) for v in raw_rows[h]]
            if len(triplets(raw_rows[h])) >= 3 and all(
                v in candidate and first.index(v) == candidate.index(v) for v in identity
            ):
                return h
            # Only band labels and empty spacer cells may intervene.
            if any(_cell(v) and not _ARROWS.match(_cell(v)) for v in raw_rows[h]):
                break
        return found

    def resolve_claims_class(self, *, row, cmap, sheet_hint, section, **context):
        def value(field):
            i = cmap.get(field)
            return clean_text(row[i]) if i is not None and i < len(row) else ""
        parent, sub = value("class"), value("subclass")
        cm = self.class_map()
        if parent and _unplaced(parent, cm) and sub and not _unplaced(sub, cm):
            # The row's own Section Description names the class: 'Marine
            # Insurance' / 'Cargo', or 2025 Q3 'INDUSTRIAL ALL RISK' / 'FIRE'.
            return sub, "column", f"{parent} / {sub}"
        if parent:
            return parent, "column", parent
        if section:
            return section, "banner", section
        return sheet_hint, "sheet", context.get("sheet") or sheet_hint

    def claims_exclude(self, field, norm):
        # Bare NET / TTY / FAC are the proportion of a (NET, NET AMOUNT) pair.
        if field in {"amount_ret", "amount_treaty", "amount_fac", "total_claims"} and norm in _PAIR_PPN:
            return True
        return super().claims_exclude(field, norm)

    def map_claims_columns(self, header, aliases, sheet_type=""):
        cm = super().map_claims_columns(header, aliases, sheet_type)
        # EMPLE writes each proportion BEFORE its amount ('RETENTION % |
        # RETENTION AMOUNT'); the shared rule pairs an amount with the share
        # column after it, which here is the next band's proportion.
        norms = [normalize_header(h) for h in header]
        for kind in ("ret", "treaty", "fac"):
            i = cm.get("amount_" + kind)
            if i is not None and i > 0 and (is_share_header(norms[i - 1]) or norms[i - 1] in _PAIR_PPN):
                cm.mapping["ppn_" + kind] = i - 1
        if "SECTION DESCRIPTION" in norms:
            cm.mapping["subclass"] = norms.index("SECTION DESCRIPTION")
        return cm

    def sheet_class_hint(self, sheet, class_hint):
        # 'FIRE TERRORISM' / 'PVT Q2' tabs: the tab-name scan stops at FIRE;
        # Manual: PVT / Terrorism is its own class, never merged with Fire.
        return "TERRORISM" if terrorism_tab(sheet) else class_hint

    def check_claims_row(self, claim, *, row, cmap, exceptions, path, sheet, excel_row):
        # Excel exports may encode an explicit year as text. Keep the source
        # year authoritative while giving it the same type as numeric years
        # and the explicit U/YR section values. Do not infer missing years or
        # reinterpret ranges/unknown text.
        if isinstance(claim.uw_yr, str) and re.fullmatch(r'(?:19|20)\d{2}', claim.uw_yr.strip()):
            claim.uw_yr = int(claim.uw_yr.strip())

    def classify_source_tab(self, file, sheet, rows):
        return retype_tables(file, sheet, rows, self.table_vocab, _emple_table_rule)

    def prepare_source_sheet(self, sheet, rows, *, path, exceptions):
        rows = ymd_dot_dates(sheet, rows, path, exceptions)
        rows = merge_quota_share_surplus_views(sheet, rows, path, exceptions)
        rows = join_split_premium_header(sheet, rows, path, exceptions)
        rows = cut_side_table(sheet, rows, path, exceptions)
        rows = name_loss_date_column(sheet, rows, path, exceptions)
        rows = name_pair_amount_headers(sheet, rows, path, exceptions)
        rows = claims_uw_sections(sheet, rows, path, exceptions, self.claims_aliases())
        return skip_unlabelled_claims_subtotals(sheet, rows, path, exceptions)


class EmpleScibAdapter(_EmpleAdapter):
    cedant = "EMPLE"
    broker = "SCIB"
    superseded_files = {
        # Manual source-of-truth: a 'Revised' file is not automatically correct.
        # This one hides FIRE, PVT FIRE, ENGINEERING, PVT ENGINEERING, the paid
        # tab and SUMMARY; its only visible tabs (MARINE CARGO, MARINE HULL) are
        # identical to the original combined workbook, which therefore controls.
        (2021, 1): {"Revised Premium Bordereaux Q1.xlsx":
                    "its visible tabs (MARINE CARGO, MARINE HULL) repeat 'Premium n Claims Borderaeux Q1.xlsx'; "
                    "every other class tab is hidden"},
        # Every row of the Fire schedule (452 claims, reserve 274,715,411.32)
        # repeats with the same reserve in the all-class TREATY schedule.
        (2022, 4): {"Fire outstanding claims AS AT DECEMBER 2022.xlsx":
                    "every claim and reserve repeats in 'TREATY outstanding claims AS AT DECEMBER 2022.xlsx'"},
        # Same 1,576 claims; 21 cells differ. The 'EMPLE GENERAL' schedule fills
        # three treaty amounts left blank in the other copy (more complete), but
        # the two disagree on the reserve of two claims — flagged for review.
        (2024, 4): {"Oustanding Loss Bordreaux as at Dec 2024.xlsx": (
            "WARN",
            "'OUTSTANDING CLAIM SCHEDULE AS AT 31ST DECEMBER 2024 - EMPLE GENERAL.xlsx' read instead (same "
            "claims; fills 3 blank treaty amounts). The copies disagree on two reserves (source rows 1576: "
            "70,000,000 vs 41,500,000; 1595: 251,617,500 vs 2,528,668,261): confirm with the cedant")},
        # Manual B: no file is marked revised; the copies were compared. Paid
        # claims are identical (67 claims, 86,198,656.63); 'Premium and Paid
        # Claims Q2' has one more HULL policy (emP/MARINE-HULL/2025/05/000003).
        (2025, 2): {"Premium and Loss Bordreaux Q2.xlsx":
                    "'Premium and Paid Claims Q2.xlsx' is the more complete copy (one more HULL risk; "
                    "paid claims identical)"},
    }


class EmpleAonAdapter(_EmpleAdapter):
    cedant = "EMPLE"
    broker = "AON"
    superseded_files = {
        (2022, 4): {"2022 fourth quarterSCIB 1.xlsx":
                    "all 8 sheets are cell-identical to '2022 fourth quarterSCIB.xlsx'"},
    }
