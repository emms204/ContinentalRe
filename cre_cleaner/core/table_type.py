"""Content-based table typing: PREMIUM / PAID / OUTSTANDING / UNKNOWN / OTHER.

IMPL-20260929-02 (Cleaning Manual C.5, D.1, Part Three step 1, Part Five step 1):
the transaction type of every table is decided from its own content — column
headers, the title/banner rows above the header, the tab name and claim-number
shaped values — never from the file name alone. The file name is only a
tie-breaker when the table itself carries no paid/outstanding evidence.

Decision (per table; a tab may hold several tables, split at each header row):
  1. FAMILY from the header signature: premium signals (SUM INSURED, GROSS
     PREMIUM, DEBIT NOTE, PERIOD OF INSURANCE, PPN / TREATY PPNn (+ adapter terms),
     COMMISSION, ...) vs claims signals (CLAIM NO, DATE OF LOSS, CLAIMANT,
     claim-no values such as CL/... or C/GITR/...). Both families, or fewer
     than 2 signals, -> UNKNOWN.
  2. PAID vs OUTSTANDING by a vote of tab name, title rows and header amount
     words (PAID / SETTLED / PAYMENT DATE vs OUTSTANDING / OST / OS / RESERVE /
     ESTIMATE and misspellings OUSTANDING, OUTSANDING, OUTS). Any disagreement
     -> UNKNOWN (sent to the exceptions sidecar, never guessed). Only when none
     of the three votes, the file name breaks the tie.
  3. No transaction header, a header without data rows, or a statement/summary
     tab -> OTHER (logged in the audit, not loaded).
Reserves are never read as paid: a reserve/estimate/outstanding word is an
OUTSTANDING vote and conflicts with any PAID vote.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

PREMIUM, PAID, OUTSTANDING, UNKNOWN, OTHER = "PREMIUM", "PAID", "OUTSTANDING", "UNKNOWN", "OTHER"
SHEET_TYPE = {PREMIUM: "premium", PAID: "paid", OUTSTANDING: "outstanding",
              UNKNOWN: "unknown", OTHER: "other"}


def _norm(v: Any) -> str:
    s = "" if v is None else str(v)
    s = s.replace("\xa0", " ").upper()
    s = re.sub(r"[^A-Z0-9%/]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Outstanding spelled every way seen in the raw files: OUTSTANDING, OUSTANDING,
# OUTSANDING, OUTSTANDIN, OUTST, OUTS, OST, O/S, OS.
_OUT_RE = re.compile(r"\bOU[T]?S[T]?A?N?D?I?N?G?S?\b|\bOUTST\w*|\bOST\b|\bO ?/ ?S\b|\bOS\b")
_PAID_RE = re.compile(r"\bPAID\b|\bPAYMENTS?\b|\bSETTLE(D|MENT)?\b|\bDISCHARGE\b|\bDV\b|\bCHEQUE\b")
_RESERVE_RE = re.compile(r"\bRESERVES?\b|\bESTIMATE[SD]?\b|\bREVISED ESTIMATE\b")
_OTHER_TAB_RE = re.compile(r"\bSTATEMENT\b|\bSUMMARY\b|\bRECAP\b")

# Generic share / proportion column words; an adapter may add its own
# abbreviations to this one alternation (TableVocab.share_terms).
_SHARE_SIGNAL = r"\bPPN\d*\b|\bPROPORTION\b|\bSHARE %|\bRATE\b"
_BAND_SIGNAL = r"\b(OWN )?RETENTION\b|\bTREATY\b|\bQUOTA\b|\bSURP"

_H_PREMIUM = [
    (r"\bGROSS PREMIUM\b", 4), (r"\bPREMIUM\b", 3),
    (r"\bDEBIT NOTE\b|\bDN ?CN\b|\bD/?N\b|\bCREDIT NOTE\b", 3),
    (r"\bPERIOD OF INSURANCE\b|\bINSURANCE PERIOD\b", 2),
    (r"\bCOMMISSION\b|\bCOMM\b|\bBROKERAGE\b", 2),
    (_SHARE_SIGNAL, 1), (r"\bMPL\b", 2),
    (r"\bEFFECTIVE DATE\b|\bINCEPTION\b|\bEXPIRY\b", 1),
    (r"\bSUM INSURED\b|\bTSI\b", 1), (r"\bCESSION\b|\bCEDED PREMIUM\b", 2), (r"\bENDORSEMENT\b", 1),
]
_H_CLAIM = [
    (r"\bCLAIM NO\b|\bCLAIM NUMBER\b|\bCLAIM REF\b|\bCLM NO\b|\bCLAIMS NO\b", 4),
    (r"\bDATE OF LOSS\b|\bLOSS DATE\b|\bDATE OF ACCIDENT\b|\bDATE OF INCIDENT\b", 4),
    (r"\bCLAIMANT\b", 2),
    (r"\bCAUSE OF LOSS\b|\bNATURE OF LOSS\b|\bDETAILS OF LOSS\b|\bPERIL\b|\bLOSS DESCRIPTION\b", 2),
    (r"\bDATE (NOTIFIED|REPORTED|OF NOTIFICATION|INTIMATED)\b|\bNOTIFICATION DATE\b", 1),
    (r"\bNET LIABILITY\b|\bOWN SHARE\b", 1),
    (r"\bTOTAL CLAIMS?\b|\bGROSS CLAIMS?\b|\bCLAIMS? AMOUNT\b", 2),
]
_H_PAID = [
    (r"\bPAID\b|\bCLAIMS? PAID\b|\bAMOUNT PAID\b|\bPAID AMOUNT\b", 4),
    (r"\bDATE (OF )?PAY(MENT)?\b|\bPAYMENT DATE\b|\bDATE PAID\b", 4),
    (r"\bSETTLE(D|MENT)\b", 3), (r"\bDISCHARGE VOUCHER\b|\bDV NO\b|\bCHEQUE\b|\bPV NO\b", 3),
    (r"\bTOTAL CLAIMS\b|\bNET LIABILITY\b", 1), (r"\bRECOVERY\b|\bSALVAGE\b", 1),
]
_H_OUT = [
    (r"\bOST\b|\bOUT ?STANDING\b|\bOUSTANDING\b|\bOUTSANDING\b|\bO/S\b|\bOS\b", 4), (r"\bRESERVES?\b", 4),
    (r"\bESTIMATE[SD]?\b|\bINCURRED\b", 3), (r"\bSTATUS\b", 1),
    (r"\bDATE (NOTIFIED|REPORTED|OF NOTIFICATION|INTIMATED)\b", 1), (r"\bOWN SHARE\b", 1),
    (r"\bOUTST\w*|\bAMOU?N?T OUTST", 4),
]


def _compile(lst):
    return [(re.compile(p), w) for p, w in lst]


_H_PREMIUM_SRC, _H_CLAIM_SRC, _H_PAID_SRC, _H_OUT_SRC = _H_PREMIUM, _H_CLAIM, _H_PAID, _H_OUT


class TableVocab:
    """Compiled header vocabulary. Generic by default; a cedant adapter adds
    its own abbreviations (``share_terms`` join the share/proportion signal,
    ``band_terms`` the RET/TREATY band-row signal) via ``table_vocab``."""

    def __init__(self, share_terms: Sequence[str] = (), band_terms: Sequence[str] = ()) -> None:
        share = "|".join([_SHARE_SIGNAL[:_SHARE_SIGNAL.index("|\\bPROPORTION")]]
                         + [rf"\b{re.escape(t)}\b" for t in share_terms]
                         + [_SHARE_SIGNAL[_SHARE_SIGNAL.index("\\bPROPORTION"):]])
        prem = [(share if p == _SHARE_SIGNAL else p, w) for p, w in _H_PREMIUM_SRC]
        self.H_PREMIUM = _compile(prem)
        self.H_CLAIM = _compile(_H_CLAIM_SRC)
        self.H_PAID = _compile(_H_PAID_SRC)
        self.H_OUT = _compile(_H_OUT_SRC)
        self.VOCAB = self.H_PREMIUM + self.H_CLAIM + self.H_PAID + self.H_OUT + _compile(
            [(r"\bPOLICY\b", 2), (r"\bINSURED\b|\bASSURED\b|\bCLIENT\b", 2)])
        band = _BAND_SIGNAL.split("|")
        band = band[:1] + [rf"\b{re.escape(t)}\b" for t in band_terms] + band[1:]
        self.BAND_RE = re.compile("|".join(band))


GENERIC_VOCAB = TableVocab()
# Claim numbers: CL/045993/..., CLM-..., C/GITR/..., .../CL/...
_CLAIMNO_VAL = re.compile(r"^(CL|CLM|CLAIM|C/)[/\- ]?\d|/CL/|^CL\d|\bCLM[/\-]|^C/[A-Z]{2,}/", re.I)

MIN_HEADER_SCORE = 6


@dataclass
class TableType:
    header_row: int            # 0-based header row index (-1: none)
    end_row: int               # exclusive end of this table's rows
    label: str
    confidence: float
    evidence: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    n_rows: int = 0

    def audit_text(self) -> str:
        r = f"r{self.header_row + 1}" if self.header_row >= 0 else "r-"
        s = f"{r} {self.label} conf={self.confidence:.1f}"
        if self.evidence:
            s += " [" + "; ".join(self.evidence) + "]"
        if self.conflicts:
            s += " CONFLICT[" + "; ".join(self.conflicts) + "]"
        return s


@dataclass
class TabType:
    file: str
    sheet: str
    label: str
    confidence: float
    evidence: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    tables: List[TableType] = field(default_factory=list)

    @property
    def sheet_type(self) -> str:
        return SHEET_TYPE[self.label]

    def audit_text(self) -> str:
        parts = [t.audit_text() for t in self.tables] or ["no table"]
        s = " | ".join(parts)
        if self.conflicts and not any(t.conflicts for t in self.tables):
            s += " CONFLICT[" + "; ".join(self.conflicts) + "]"
        return s


def _filled(row: Sequence[Any]) -> List[Any]:
    return [c for c in row if c not in (None, "") and str(c).strip()]


def _cells(row: Sequence[Any]) -> List[str]:
    return [n for n in (_norm(c) for c in row if isinstance(c, str)) if n]


def _row_score(cells: Sequence[str], vocab: Optional[TableVocab] = None) -> int:
    return sum(w for c in cells for rx, w in (vocab or GENERIC_VOCAB).VOCAB if rx.search(c))


def _score(cells: Sequence[str], sig) -> Tuple[int, List[str]]:
    total, hits = 0, []
    for c in cells:
        for rx, w in sig:
            if rx.search(c):
                total += w
                hits.append(c)
                break
    return total, hits


def find_table_header(rows: Sequence[Sequence[Any]], start: int = 0, scan: int = 40,
                      vocab: Optional[TableVocab] = None) -> Tuple[int, int]:
    """(row index, vocabulary score) of the best header row in rows[start:start+scan]."""
    best, best_sc = -1, 0
    for i in range(start, min(len(rows), start + scan)):
        cells = _cells(rows[i])
        if len(cells) < 3:
            continue
        sc = _row_score(cells, vocab)
        if sc > best_sc:
            best, best_sc = i, sc
    return best, best_sc


def table_header_rows(rows: Sequence[Sequence[Any]], vocab: Optional[TableVocab] = None) -> List[int]:
    """Header row of every table in a tab: the best header, then each later row
    that repeats a header signature (a second-row header directly under a
    header belongs to the same table)."""
    hdr, hsc = find_table_header(rows, vocab=vocab)
    if hdr < 0 or hsc < MIN_HEADER_SCORE:
        return []
    out = [hdr]
    need = max(MIN_HEADER_SCORE, hsc * 0.6)
    for i in range(hdr + 1, len(rows)):
        cells = _cells(rows[i])
        if len(cells) >= 3 and _row_score(cells, vocab) >= need and i != out[-1] + 1:
            out.append(i)
    return out


def _explicit(text: str) -> Optional[str]:
    n = _norm(text)
    o = bool(_OUT_RE.search(n) or _RESERVE_RE.search(n))
    p = bool(_PAID_RE.search(n))
    if o and not p:
        return OUTSTANDING
    if p and not o:
        return PAID
    return None


def classify_table(file: str, sheet: str, rows: Sequence[Sequence[Any]], hdr: int,
                   end: Optional[int] = None, title_start: int = 0,
                   inherited_title: str = "", vocab: Optional[TableVocab] = None) -> TableType:
    """Type of the table whose header is rows[hdr] and whose data run to `end`.

    `inherited_title` is the tab's own title (rows above its first table): a
    sheet banner such as '2ND SURP PAID CLAIM' covers every table below it.
    """
    end = len(rows) if end is None else end
    v = vocab or GENERIC_VOCAB
    if hdr < 0:
        n = sum(1 for r in rows if len(_filled(r)) >= 3)
        _h, sc = find_table_header(rows, vocab=v)
        # No transaction vocabulary at all (statement, reinsurer summary) -> OTHER;
        # rows with a weak, partial header -> UNKNOWN (sent to exceptions).
        if n == 0 or sc == 0:
            return TableType(-1, end, OTHER, 1.0, [f"no transaction header (score {sc}, {n} rows)"])
        return TableType(-1, end, UNKNOWN, 0.0, [f"no transaction header (best score {sc})"],
                         [f"{n} rows without a recognisable header"], n)
    header = _cells(rows[hdr])
    band = _cells(rows[hdr - 1]) if hdr > 0 else []
    below = _cells(rows[hdr + 1]) if hdr + 1 < end else []
    lo = max(title_start, hdr - 6)
    if inherited_title:
        # later table: its own banner rows only (short rows directly above the
        # header), never the previous table's data rows.
        lo = hdr
        while lo - 1 >= max(title_start, hdr - 6) and len(_filled(rows[lo - 1])) < 3:
            lo -= 1
    title = " | ".join(c for r in rows[lo:hdr] for c in _cells(r))
    if inherited_title:
        title = inherited_title + (" | " + title if title else "")
    data = [r for r in rows[hdr + 1:end] if len(_filled(r)) >= 3]
    if not data:
        return TableType(hdr, end, OTHER, 1.0, ["header but no data rows"], n_rows=0)
    vals = [c for r in data[:200] for c in r if isinstance(c, str)]
    claimno_rows = min(sum(1 for v in vals if _CLAIMNO_VAL.search(v.strip())), len(data))
    cells = header + below
    p, ph = _score(cells, v.H_PREMIUM)
    c, ch = _score(cells, v.H_CLAIM)
    pd, pdh = _score(cells + band, v.H_PAID)
    o, oh = _score(cells + band, v.H_OUT)
    claimno = claimno_rows >= max(1, 0.3 * len(data))
    claim_side = c + (4 if claimno else 0)
    band_hit = [b for b in band if v.BAND_RE.search(b)]
    ev = [f"premium signals={p} {ph[:4]}" + (f" band={band_hit[:3]}" if band_hit else ""),
          f"claims signals={claim_side} {ch[:4]}" + (f" +{claimno_rows} claim-no values" if claimno else "")]
    n = len(data)
    if p >= 5 and claim_side < 4:
        fam = PREMIUM
    elif claim_side >= 4 and (p < 5 or claim_side >= p):
        fam = "CLAIM"
    else:
        return TableType(hdr, end, UNKNOWN, 0.0, ev, ["family ambiguous (premium and claims signals)"], n)
    hits = len(ph) + (1 if band_hit else 0) if fam == PREMIUM else len(ch) + (1 if claimno else 0)
    if hits < 2:
        return TableType(hdr, end, UNKNOWN, 0.0, ev, [f"only {hits} {fam.lower()} signal"], n)
    tab_vote = _explicit(sheet)
    if fam == PREMIUM:
        if tab_vote:
            return TableType(hdr, end, UNKNOWN, 0.0, ev, [f"tab name says {tab_vote} but headers are premium"], n)
        return TableType(hdr, end, PREMIUM, 1.0, ev, n_rows=n)
    hv = PAID if pd > o and pd >= 3 else (OUTSTANDING if o > pd and o >= 3 else None)
    votes = {"tab name": tab_vote, "title rows": _explicit(title), "header words": hv}
    got = {k: v for k, v in votes.items() if v}
    ev.append(f"paid words={pd} {pdh[:2]} outstanding words={o} {oh[:2]}")
    if len(set(got.values())) > 1:
        return TableType(hdr, end, UNKNOWN, 0.0, ev, [f"paid/outstanding conflict {got}"], n)
    if got:
        lab = next(iter(got.values()))
        ev.append(f"agree: {got}")
        return TableType(hdr, end, lab, 0.9 if len(got) > 1 else 0.7, ev, n_rows=n)
    fv = _explicit(str(file).replace("\\", "/").split("/")[-1])
    if fv:
        ev.append(f"file name says {fv} (tie-break only)")
        return TableType(hdr, end, fv, 0.5, ev, n_rows=n)
    return TableType(hdr, end, UNKNOWN, 0.0, ev, ["claims table but no paid/outstanding evidence"], n)


def classify_tab(file: str, sheet: str, rows: Sequence[Sequence[Any]],
                 vocab: Optional[TableVocab] = None) -> TabType:
    """Type every table in a tab; the tab type is their common type.

    Tables that disagree (e.g. a premium and a claims table, or a paid and an
    outstanding table in one tab) make the tab UNKNOWN with the conflict.
    """
    rows = list(rows)
    if _OTHER_TAB_RE.search(_norm(sheet)):
        t = TableType(-1, len(rows), OTHER, 1.0, ["statement/summary tab"])
        return TabType(file, sheet, OTHER, 1.0, t.evidence, [], [t])
    heads = table_header_rows(rows, vocab)
    if not heads:
        t = classify_table(file, sheet, rows, -1, vocab=vocab)
        return TabType(file, sheet, t.label, t.confidence, t.evidence, t.conflicts, [t])
    tab_title = " | ".join(c for r in rows[max(0, heads[0] - 6):heads[0]] for c in _cells(r))
    tables = []
    for k, h in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(rows)
        start = heads[k - 1] + 1 if k else 0
        tables.append(classify_table(file, sheet, rows, h, end, title_start=start,
                                     inherited_title=tab_title if k else "", vocab=vocab))
    first = tables[0]
    txn = [t for t in tables if t.label not in (OTHER,)]
    labels = {t.label for t in txn}
    if not txn:
        return TabType(file, sheet, OTHER, 1.0, first.evidence, [], tables)
    if len(labels) == 1:
        lab = labels.pop()
        conf = min(t.confidence for t in txn)
        return TabType(file, sheet, lab, conf, txn[0].evidence, [c for t in txn for c in t.conflicts], tables)
    conflicts = [f"tables disagree: {', '.join(f'r{t.header_row + 1}={t.label}' for t in txn)}"]
    conflicts += [c for t in txn for c in t.conflicts]
    return TabType(file, sheet, UNKNOWN, 0.0, first.evidence, conflicts, tables)
