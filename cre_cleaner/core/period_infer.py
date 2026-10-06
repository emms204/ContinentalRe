"""Infer calendar year and quarter from bordereau content, then filenames.

Order of evidence (IMPL-20260929-05):

1. Report banners in the first rows of a sheet: a bracketed "From:[April 01,
   2023 ] To [ June 30, 2023 ]" period-date range, a plain From/To or
   "Period Date" range, whose two dates fall in one quarter, an "As At
   [September 30, 2024]" date, or a quarter label with a year ("Q2, 2024
   CLAIMS BORDEREAU"). A banner is the reporting period (confidence high);
   claims often hold historical losses outside the bordereau quarter, so row
   dates are weaker.
2. Date values in columns whose headers name a date (header keywords are
   matched as whole words: "TO" never matches TOTAL or SECTOR). When a sheet
   has reporting-date columns (transaction / payment / effective date), only
   those vote; cover and loss dates are historical there.
3. The file name. If the content guess is below high confidence and the file
   name clearly names a different quarter or year, the file name wins
   (``filename_override`` → WARN period_filename_override). If neither
   settles year and quarter the result is not ``ok`` (ERROR period_ambiguous
   in the pipeline) — a quarter is never guessed.

Evidence combination when a workbook has report banners (IMPL-20260929-08):

* Row dates. Per sheet the IMPL-05 priority picks the row-date columns:
  reporting-date columns (paid / payment / transaction / entry / effective
  date) when present, else loss-date columns. When more than half of those
  dated rows (at least 5) fall in one year, that year is the strongest year
  evidence. Reporting-date rows beat any banner. Loss dates lag the report
  (old losses stay on a bordereau), so a loss-date year beats a banner only
  when the banner is earlier than it.
* Banners are de-duplicated: the same banner text repeated on several tabs is
  one vote. A banner is stale when its year is more than 1 year from the row-
  date reference year (the latest year holding >= 25% of the dated rows; for
  loss dates only banners *earlier* than that are stale). Stale banners are
  ignored (INFO period_banner_stale).
* Per field: quarter = a precise non-stale banner (From/To range or As At),
  else the file name quarter, else the non-stale banner labels; year = the
  reporting-date row year, else non-stale banners, else the loss-date row
  year, else the file name. Remaining disagreement is WARN
  period_banner_conflict; when nothing settles it the result is not ``ok``
  (ERROR period_ambiguous). A year is never picked silently.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from cre_cleaner.adapters.base import (
    EXCEL_SUFFIXES,
    name_tokens,
    quarters_in_text,
    token_month,
    years_in_text,
)
from cre_cleaner.config import QUARTER_MONTHS
from cre_cleaner.core.normalize import clean_text, normalize_header, parse_date

_MONTH_TO_Q = {m: q for q, months in QUARTER_MONTHS.items() for m in months}

# Higher weight = stronger *reporting-period* signal.
# Date of Loss is weak: claims bordereaux often hold historical losses.
_DATE_COL_WEIGHTS: Tuple[Tuple[str, float], ...] = (
    ("TRANSACTION DATE", 3.0),
    ("TRANS DATE", 3.0),
    ("TRANS. DATE", 3.0),
    ("COVER FROM", 2.5),
    ("PERIOD FROM", 2.5),
    ("START DATE", 2.5),
    ("COVER START", 2.5),
    ("INCEPTION", 2.5),
    ("FROM", 2.0),
    ("PERIOD OF COVER", 2.0),
    ("PERIOD OF INSURANCE", 2.0),
    ("INSURANCE PERIOD", 2.0),
    ("PAYMENT DATE", 1.5),
    ("PAID DATE", 1.5),
    ("NOTIFICATION DATE", 1.0),
    ("REGISTERED DATE", 1.0),
    ("DATE OF LOSS", 0.4),
    ("LOSS DATE", 0.4),
    ("DOL", 0.4),
    ("COVER TO", 0.3),
    ("PERIOD TO", 0.3),
    ("END DATE", 0.3),
    ("COVER END", 0.3),
    ("EXPIRY", 0.3),
    ("TO", 0.2),
)

# Reporting-date columns: when a sheet has one, only these vote (cover start
# and loss dates on a claims or premium listing are historical).
_REPORTING_DATE_HEADERS: Tuple[str, ...] = (
    "TRANSACTION DATE", "TRANS DATE", "PAYMENT DATE", "PAID DATE", "DATE PAID",
    "DATE CLAIM PAID", "CLAIM PAID DATE", "EFFECTIVE DATE",
)
_REPORTING_DATE_WEIGHT = 3.0

# Row-date evidence for the year (IMPL-20260929-08). Reporting-date columns
# first (IMPL-05 priority), entry date included; loss dates otherwise.
_ROW_REPORTING_HEADERS: Tuple[str, ...] = _REPORTING_DATE_HEADERS + (
    "ENTRY DATE", "DATE OF ENTRY", "DATE ENTERED",
)
_ROW_LOSS_HEADERS: Tuple[str, ...] = ("DATE OF LOSS", "LOSS DATE", "DOL")
_ROW_MIN_MAJORITY = 5      # dated rows needed before a row-date year is decisive
_ROW_MIN_REFERENCE = 2     # dated rows needed for the staleness reference year
_ROW_REFERENCE_SHARE = 0.25
_STALE_YEARS = 1

_YEAR_ONLY_HEADERS = {
    "UNDERWRITING YEAR", "UDW YEAR", "UW YEAR", "U/W YEAR", "UW YR", "YEAR",
}


@dataclass
class PeriodInference:
    year: Optional[int] = None
    quarter: Optional[int] = None
    confidence: str = "none"  # high | medium | low | none
    source: str = "none"  # content | filename | mixed | none
    evidence: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # Set when the file name replaced the content guess:
    # "content 2023 Q1 (medium) -> filename 2023 Q2".
    filename_override: str = ""
    # (severity, reason, detail) records for the pipeline, e.g.
    # ("WARN", "period_banner_conflict", "...") or ("INFO", "period_banner_stale", ...).
    notes: List[Tuple[str, str, str]] = field(default_factory=list)
    # True when banners, row dates and the file name were already combined
    # per field (IMPL-08): infer_period returns it as-is.
    combined: bool = False

    @property
    def ok(self) -> bool:
        return self.year is not None and self.quarter is not None


def _vote(counter: Counter) -> Tuple[Optional[object], float]:
    if not counter:
        return None, 0.0
    [(val, n)] = counter.most_common(1)
    return val, float(n)


def _quarter_of(d: date) -> int:
    return (d.month - 1) // 3 + 1


def _tokens(text: str) -> List[str]:
    return [t for t in re.split(r"[^A-Z0-9/]+", str(text).upper()) if t]


def header_has_phrase(header: str, phrase: str) -> bool:
    """True when ``phrase`` occurs in ``header`` as whole words (a contiguous
    token run): 'TO' matches 'COVER TO' but not 'TOTAL' or 'SECTOR'."""
    ht, pt = _tokens(normalize_header(header)), _tokens(phrase)
    if not ht or not pt:
        return False
    n = len(pt)
    return any(ht[i:i + n] == pt for i in range(len(ht) - n + 1))


# Period-date banner: "From:[April 01, 2023 ] To [ June 30, 2023 ]"
# Require closing brackets so the TO date is not truncated to one letter.
_FROM_TO_BANNER_RE = re.compile(
    r"FROM\s*:?\s*\[\s*(?P<from>[^\]]+?)\s*\]\s*"
    r"(?:TO|–|-|—)\s*\[\s*(?P<to>[^\]]+?)\s*\]",
    re.IGNORECASE,
)
_FROM_TO_BANNER_PLAIN_RE = re.compile(
    r"FROM\s*:?\s*(?P<from>\d{1,2}[\s\-]\w+[\s\-]\d{4}|\w+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})"
    r"\s+(?:TO|–|-|—)\s+"
    r"(?P<to>\d{1,2}[\s\-]\w+[\s\-]\d{4}|\w+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def _period_from_from_to_banner(text: str) -> Optional[Tuple[int, int]]:
    """Reporting quarter from a From/To period banner (not policy cover dates)."""
    s = clean_text(text)
    if not s or "FROM" not in s.upper():
        return None
    m = _FROM_TO_BANNER_RE.search(s) or _FROM_TO_BANNER_PLAIN_RE.search(s)
    if not m:
        return None
    a = parse_date(m.group("from").strip(" []"))
    b = parse_date(m.group("to").strip(" []"))
    if a is None or b is None:
        return None
    da = a.date() if isinstance(a, datetime) else a
    db = b.date() if isinstance(b, datetime) else b
    if da.year != db.year:
        return None
    if not (1990 <= da.year <= 2100) or da > db:
        return None
    qa, qb = _quarter_of(da), _quarter_of(db)
    if qa == qb:
        return (da.year, qa)
    # A range spanning quarters (e.g. a full year) is not a quarterly
    # reporting period — ignored, never mapped to its first quarter.
    return None


def _header_date_weight(header: str) -> float:
    h = normalize_header(header)
    if not h:
        return 0.0
    best = 0.0
    for needle, w in _DATE_COL_WEIGHTS:
        if header_has_phrase(h, needle):
            best = max(best, w)
    if _is_reporting_date_header(h):
        best = max(best, _REPORTING_DATE_WEIGHT)
    return best


def _is_reporting_date_header(header: str) -> bool:
    return any(header_has_phrase(header, p) for p in _REPORTING_DATE_HEADERS)


# Dates written in report banners: "April 01, 2023", "01/04/2023",
# "2023-04-01", "01-Apr-2023", "1 April 2023".
_MONTHS_RE = (r"(?:JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUNE?|JULY?|"
              r"AUG(?:UST)?|SEP(?:T(?:EMBER)?)?|OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)")
_BANNER_DATE_RE = re.compile(
    r"(" + _MONTHS_RE + r"\s+\d{1,2}(?:ST|ND|RD|TH)?,?\s+(?:19|20)\d{2}"
    r"|\d{1,2}(?:ST|ND|RD|TH)?[\s\-]+" + _MONTHS_RE + r",?[\s\-]+(?:19|20)\d{2}"
    r"|\d{1,2}[/.\-]\d{1,2}[/.\-](?:19|20)\d{2}"
    r"|(?:19|20)\d{2}-\d{1,2}-\d{1,2})",
    re.IGNORECASE,
)


def _banner_dates(text: str) -> List[date]:
    out: List[date] = []
    for m in _BANNER_DATE_RE.finditer(text):
        raw = re.sub(r"(?<=\d)(ST|ND|RD|TH)\b", "", m.group(1), flags=re.IGNORECASE)
        raw = re.sub(r"\s+", " ", raw.replace("-", " ") if re.search(r"[A-Za-z]", raw) else raw).strip()
        d = parse_date(raw)
        if d is None:
            for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"):
                try:
                    d = datetime.strptime(raw.title(), fmt)
                    break
                except ValueError:
                    continue
        if d is not None and 1990 <= d.year <= 2100:
            out.append(d.date() if isinstance(d, datetime) else d)
    return out


def banner_range_period(text: str) -> Optional[Tuple[int, int, str]]:
    """(year, quarter, kind) from a report banner, else None.

    * "From … To …" (or a "Period Date" row) with two dates in the SAME
      quarter → that quarter (kind "range"). A range spanning quarters is not
      a quarterly reporting period → None.
    * "As At [date]" with one date → the quarter of that date (kind "as_at").
    """
    u = " ".join(str(text).replace("\xa0", " ").split()).upper()
    toks = set(_tokens(u))
    dates = _banner_dates(u)
    if len(dates) >= 2 and (("FROM" in toks and "TO" in toks) or "PERIOD" in toks):
        a, b = dates[0], dates[1]
        if a <= b and a.year == b.year and _quarter_of(a) == _quarter_of(b):
            return a.year, _quarter_of(a), "range"
        return None
    if len(dates) == 1 and re.search(r"\bAS\s*AT\b", u):
        d = dates[0]
        return d.year, _quarter_of(d), "as_at"
    return None


def _is_year_only_header(header: str) -> bool:
    h = normalize_header(header)
    return h in _YEAR_ONLY_HEADERS or h.endswith(" YEAR") and "UNDERWRITING" in h


def _iter_excel_paths(raw_dir: Path) -> List[Path]:
    raw_dir = Path(raw_dir)
    if raw_dir.is_file():
        return [raw_dir] if raw_dir.suffix.lower() in EXCEL_SUFFIXES else []
    return sorted(
        p for p in raw_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in EXCEL_SUFFIXES
        and not p.name.startswith("~$")
        and not p.name.startswith(".")
    )


def _collect_dates_from_sheet(
    rows: Sequence[Sequence[Any]],
    *,
    max_header_scan: int = 30,
    max_data_rows: int = 400,
    detail: Optional[dict] = None,
) -> Tuple[Counter, Counter, List[str], Optional[Tuple[int, int]]]:
    """Return (year_votes, quarter_votes, evidence, banner_period).

    ``banner_period`` is (year, quarter) when a title row names both — that is
    the reporting period and must beat historical Date-of-Loss votes.

    ``detail`` (optional dict) is filled with ``banner_kind`` (range / as_at /
    label), ``banner_text`` and ``row_years`` / ``row_tier``: one year count per
    dated data row in the row-date columns (reporting tier, else loss tier).
    """
    if detail is None:
        detail = {}
    detail.update(banner_kind=None, banner_text="", row_years=Counter(), row_tier=None)
    year_votes: Counter = Counter()
    quarter_votes: Counter = Counter()
    evidence: List[str] = []
    banner_period: Optional[Tuple[int, int]] = None
    if not rows:
        return year_votes, quarter_votes, evidence, banner_period

    # Banner / title rows above the header often name the reporting quarter
    # ("Q2, 2024 …") or the report date range ("From: [April 01, 2023] To
    # [June 30, 2023]", "As At [September 30, 2024]").
    for r in rows[:12]:
        text = " ".join(str(c) for c in r if c not in (None, ""))
        if not text.strip():
            continue
        rng = _period_from_from_to_banner(text)
        if rng is None:
            for c in r:
                if isinstance(c, str) and "FROM" in c.upper():
                    rng = _period_from_from_to_banner(c)
                    if rng is not None:
                        break
        if rng is not None:
            banner_period = rng
            detail.update(banner_kind="range", banner_text=text)
            evidence.append(
                f"period-date banner {text[:90]!r} → {rng[0]} Q{rng[1]}"
            )
            break
        rng = banner_range_period(text)
        if rng is not None:
            y, q, kind = rng
            banner_period = (int(y), int(q))
            detail.update(banner_kind=kind, banner_text=text)
            evidence.append(f"banner {kind} {' '.join(text.split())[:90]!r} → {y} Q{q}")
            break
        qs = quarters_in_text(text)
        ys = years_in_text(text)
        if qs and ys:
            # One clear reporting label (e.g. "2024 CLAIMS BORDEREAU - Q2, 2024")
            y = max(ys) if len(ys) <= 2 else Counter(ys).most_common(1)[0][0]
            q = sorted(qs)[0] if len(qs) == 1 else Counter(qs).most_common(1)[0][0]
            banner_period = (int(y), int(q))
            detail.update(banner_kind="label", banner_text=text)
            evidence.append(f"banner {text[:80]!r} → {y} Q{q}")
            break

    header_idx = None
    col_weights: dict[int, float] = {}
    year_cols: List[int] = []
    for i, row in enumerate(rows[:max_header_scan]):
        weights = {}
        ycols = []
        for j, cell in enumerate(row):
            if cell is None or cell == "":
                continue
            w = _header_date_weight(str(cell))
            if w > 0:
                weights[j] = w
            elif _is_year_only_header(str(cell)):
                ycols.append(j)
        if len(weights) >= 1 or ycols:
            if header_idx is None or sum(weights.values()) > sum(col_weights.values()):
                header_idx = i
                col_weights = weights
                year_cols = ycols

    _row_year_counts(rows, header_idx, max_header_scan, max_data_rows, detail)

    if header_idx is None:
        return year_votes, quarter_votes, evidence, banner_period

    # A sheet with reporting-date columns votes with those only.
    hdr_row = rows[header_idx]
    reporting = {j: w for j, w in col_weights.items()
                 if j < len(hdr_row) and _is_reporting_date_header(str(hdr_row[j] or ""))}
    if reporting:
        col_weights = reporting
    evidence.append(
        f"header row {header_idx + 1}: date cols "
        + ", ".join(f"{c + 1}(w={col_weights[c]})" for c in sorted(col_weights)[:8])
        + (" (reporting-date columns only)" if reporting else "")
    )

    scanned = 0
    for row in rows[header_idx + 1: header_idx + 1 + max_data_rows]:
        if scanned >= max_data_rows:
            break
        if not any(c not in (None, "") for c in row):
            continue
        scanned += 1
        for j, w in col_weights.items():
            if j >= len(row):
                continue
            d = parse_date(row[j])
            if d is None:
                from cre_cleaner.core.normalize import parse_period
                a, _b = parse_period(row[j])
                d = a
            if d is None or not (1990 <= d.year <= 2100):
                continue
            year_votes[d.year] += w
            quarter_votes[_quarter_of(d)] += w
        for j in year_cols:
            if j >= len(row):
                continue
            cell = row[j]
            if isinstance(cell, (int, float)) and 1990 <= int(cell) <= 2100:
                year_votes[int(cell)] += 1.0
            else:
                ys = years_in_text(str(cell or ""))
                for y in ys:
                    year_votes[y] += 1.0

    return year_votes, quarter_votes, evidence, banner_period


def _row_year_counts(rows, header_idx, max_header_scan, max_data_rows, detail) -> None:
    """One year count per dated data row in the row-date columns (IMPL-08)."""
    def cols(row, headers):
        return [j for j, c in enumerate(row)
                if c not in (None, "") and any(header_has_phrase(str(c), h) for h in headers)]

    cand = [header_idx] if header_idx is not None else list(range(min(len(rows), max_header_scan)))
    for hi in cand:
        hdr = rows[hi]
        rep_cols, loss_cols = cols(hdr, _ROW_REPORTING_HEADERS), cols(hdr, _ROW_LOSS_HEADERS)
        use, tier = (rep_cols, "reporting") if rep_cols else (loss_cols, "loss")
        if not use:
            continue
        counts: Counter = Counter()
        for row in rows[hi + 1: hi + 1 + max_data_rows]:
            for j in use:
                if j >= len(row) or row[j] in (None, ""):
                    continue
                d = parse_date(row[j])
                if d is not None and 1990 <= d.year <= 2100:
                    counts[d.year] += 1
                    break
        if counts:
            detail.update(row_years=counts, row_tier=tier)
            return


def infer_period_from_workbook_content(
    paths: Sequence[Path],
    named: Optional[Tuple[Optional[int], Optional[int], List[str]]] = None,
) -> PeriodInference:
    """Majority vote over date-column values across Excel workbooks.

    An in-sheet banner that names year + quarter (e.g. "Q2, 2024 CLAIMS
    BORDEREAU") is the reporting period and wins over row Date-of-Loss votes.
    """
    from cre_cleaner.io.excel import read_workbook_sheets

    year_votes: Counter = Counter()
    quarter_votes: Counter = Counter()
    evidence: List[str] = []
    banners: List[Tuple[Tuple[int, int], str, str, str]] = []
    row_rep: Counter = Counter()
    row_loss: Counter = Counter()
    files_ok = 0

    for path in paths:
        try:
            sheets = read_workbook_sheets(path)
        except Exception as e:
            evidence.append(f"{path.name}: unreadable ({e})")
            continue
        file_hits = 0
        for sn, rows in sheets.items():
            det: dict = {}
            yv, qv, ev, banner = _collect_dates_from_sheet(rows, detail=det)
            if banner is not None:
                banners.append((banner, det.get("banner_kind") or "label",
                                det.get("banner_text") or "", f"{path.name}/{sn}"))
            if det.get("row_tier") == "reporting":
                row_rep.update(det["row_years"])
            elif det.get("row_tier") == "loss":
                row_loss.update(det["row_years"])
            if yv or qv or banner is not None:
                file_hits += 1
                year_votes.update(yv)
                quarter_votes.update(qv)
                for e in ev[:2]:
                    evidence.append(f"{path.name}/{sn}: {e}")
        if file_hits:
            files_ok += 1

    # Banners present: combine banners, row dates and the file name per field.
    if banners:
        return _combine_evidence(banners, row_rep, row_loss, named, evidence,
                                 year_votes, quarter_votes)

    year, y_w = _vote(year_votes)
    quarter, q_w = _vote(quarter_votes)
    result = PeriodInference(
        year=int(year) if year is not None else None,
        quarter=int(quarter) if quarter is not None else None,
        source="content",
        evidence=evidence[:24],
    )
    if result.ok:
        result.confidence = "high" if y_w >= 10 and q_w >= 10 else "medium"
        result.evidence.insert(
            0,
            f"content vote: year={result.year} (w={y_w:.1f}) "
            f"Q{result.quarter} (w={q_w:.1f}) across {files_ok} file(s)",
        )
    elif result.year is not None and result.quarter is None:
        result.warnings.append(
            f"Date columns gave year {result.year} but no clear quarter"
        )
        result.confidence = "low"
    else:
        result.warnings.append("No usable dates found in workbook date columns")
        result.confidence = "none"
        result.source = "none"
    return result


def _combine_evidence(banners, row_rep: Counter, row_loss: Counter, named,
                      evidence: List[str], year_votes: Counter,
                      quarter_votes: Counter) -> PeriodInference:
    """Per-field period from banners + row dates + file name (IMPL-08).

    See the module docstring for the order. Every override or discarded
    banner is logged in ``notes``; unresolved conflicts leave the field None.
    """
    ny, nq, nev = named or (None, None, [])
    notes: List[Tuple[str, str, str]] = []
    head: List[str] = []

    # --- row-date evidence --------------------------------------------------
    rows, tier = Counter(), None
    if sum(row_rep.values()) >= _ROW_MIN_REFERENCE:
        rows, tier = row_rep, "reporting"
    elif sum(row_loss.values()) >= _ROW_MIN_REFERENCE:
        rows, tier = row_loss, "loss"
    n = sum(rows.values())
    ref = maj = None
    if tier:
        top, top_n = rows.most_common(1)[0]
        bulk = [y for y, c in rows.items() if c / n >= _ROW_REFERENCE_SHARE]
        ref = max(bulk) if bulk else top
        if n >= _ROW_MIN_MAJORITY and top_n / n > 0.5:
            maj = top
        spread = ", ".join(f"{y}:{c}" for y, c in sorted(rows.items(), reverse=True)[:6])
        head.append(f"row dates ({tier} columns): {n} dated rows [{spread}]; "
                    f"reference year {ref}; majority year {maj or 'none'}")

    # --- banners: one vote per distinct text, stale ones dropped ------------
    uniq: dict = {}
    for (y, q), kind, text, where in banners:
        key = (" ".join(str(text).split()).upper(), int(y), int(q))
        uniq.setdefault(key, {"y": int(y), "q": int(q), "kind": kind,
                              "text": key[0], "where": []})["where"].append(where)
    live, stale = [], []
    for b in uniq.values():
        too_old = ref is not None and b["y"] < ref - _STALE_YEARS
        too_new = tier == "reporting" and ref is not None and b["y"] > ref + _STALE_YEARS
        before_losses = tier == "loss" and maj is not None and b["y"] < maj
        (stale if (too_old or too_new or before_losses) else live).append(b)

    def _desc(bs):
        return "; ".join(f"{b['text'][:60]!r} → {b['y']} Q{b['q']} "
                         f"({len(b['where'])} tab(s))" for b in bs)

    if stale:
        notes.append(("INFO", "period_banner_stale",
                      f"banner(s) ignored as stale against row-date year {maj or ref} "
                      f"({tier} dates): {_desc(stale)}"))
    if live:
        head.append(f"live banner(s): {_desc(live)}")

    # --- year ---------------------------------------------------------------
    year, ysrc, conflict = None, "", []
    precise = [b for b in live if b["kind"] in ("range", "as_at")]
    tally = Counter(b["y"] for b in (precise or live))
    if tier == "reporting" and maj is not None:
        year, ysrc = maj, f"reporting-date rows ({rows[maj]}/{n})"
        other = sorted({b["y"] for b in live} - {maj})
        if other:
            conflict.append(f"banner year(s) {other} disagree with the reporting-date "
                            f"rows ({maj}); row-date year used")
    elif tally:
        ranked = tally.most_common()
        if len(ranked) == 1:
            year, ysrc = ranked[0][0], "banner"
        elif ranked[0][1] > ranked[1][1]:
            year, ysrc = ranked[0][0], "banner majority"
            conflict.append(f"banners name years {sorted(tally)}; {year} named by most "
                            "distinct banners")
        elif ny is not None and ny in tally:
            year, ysrc = ny, "file name (banner tie)"
            conflict.append(f"banners tie on years {sorted(tally)}; file name year {ny} used")
        elif maj is not None and maj in tally:
            year, ysrc = maj, "loss-date rows (banner tie)"
            conflict.append(f"banners tie on years {sorted(tally)}; loss-date year {maj} used")
        else:
            conflict.append(f"banners tie on years {sorted(tally)} and nothing else settles "
                            "the year")
    elif maj is not None:
        year, ysrc = maj, f"{tier}-date rows ({rows[maj]}/{n})"
    elif ny is not None:
        year, ysrc = ny, "file name"
    else:
        vy, _w = _vote(year_votes)
        if vy is not None:
            year, ysrc = int(vy), "date-column vote"
            if ref is not None and year != ref:
                conflict.append(f"date-column vote {year} differs from the {tier}-date "
                                f"reference year {ref}")

    # --- quarter ------------------------------------------------------------
    quarter, qsrc = None, ""
    same = [b for b in live if year is not None and b["y"] == year]
    pq = sorted({b["q"] for b in same if b["kind"] in ("range", "as_at")})
    lq = Counter(b["q"] for b in same if b["kind"] == "label")
    if len(pq) == 1:
        quarter, qsrc = pq[0], "banner range"
        if nq is not None and nq != quarter:
            conflict.append(f"file name says Q{nq} but the report banner range says "
                            f"Q{quarter}; banner used")
    elif len(pq) > 1:
        if nq in pq:
            quarter, qsrc = nq, "file name (banner ranges disagree)"
        conflict.append(f"banner ranges name quarters {pq}"
                        + (f"; file name Q{nq} used" if quarter else ""))
    elif nq is not None:
        quarter, qsrc = nq, "file name"
        off = sorted(set(lq) - {nq})
        if off:
            notes.append(("INFO", "period_banner_stale",
                          f"file name quarter Q{nq} used; banner label(s) naming "
                          f"Q{'/Q'.join(map(str, off))} for {year} ignored"))
    elif lq:
        ranked = lq.most_common()
        if len(ranked) == 1:
            quarter, qsrc = ranked[0][0], "banner"
        elif ranked[0][1] > ranked[1][1]:
            quarter, qsrc = ranked[0][0], "banner majority"
            conflict.append(f"banners name quarters {sorted(lq)} for {year}; "
                            f"Q{quarter} named by most distinct banners")
        else:
            conflict.append(f"banners tie on quarters {sorted(lq)} for {year}")
    elif year is not None:
        vq, qw = _vote(quarter_votes)
        total = sum(quarter_votes.values())
        if vq is not None and total and qw / total > 0.5:
            quarter, qsrc = int(vq), "row dates"
            if live:
                conflict.append(f"no live banner names {year}; quarter Q{quarter} from row dates")
        elif live or stale:
            conflict.append(f"no banner, file name or clear row-date quarter for {year}")

    out = PeriodInference(
        year=year, quarter=quarter, source="content", combined=True,
        evidence=[f"period {year} Q{quarter}: year from {ysrc or 'nothing'}, "
                  f"quarter from {qsrc or 'nothing'}"] + head + nev + evidence[:16],
    )
    if conflict:
        notes.append(("WARN", "period_banner_conflict", "; ".join(conflict)))
    out.notes = notes
    if not out.ok:
        out.confidence = "none" if year is None else "low"
        out.warnings.append("Report banners, row dates and the file name do not settle the "
                            f"period (year={year}, quarter={quarter})")
    else:
        out.confidence = "medium" if conflict else "high"
        if ysrc == "file name" or qsrc == "file name":
            out.source = "mixed"
    return out


def infer_period_from_labels(labels: Iterable[str]) -> PeriodInference:
    """Weak fallback: majority vote across filename / folder labels."""
    year_votes: Counter = Counter()
    quarter_votes: Counter = Counter()
    evidence: List[str] = []

    for label in labels:
        years = years_in_text(label)
        qs = quarters_in_text(label)
        toks = name_tokens(label)
        months = {m for m in (token_month(t) for t in toks) if m}
        if len(years) == 2 and abs(max(years) - min(years)) == 1 and qs:
            y = max(years)
            year_votes[y] += 2
            evidence.append(f"{label!r}: fiscal {sorted(years)} → {y}")
        else:
            for y in years:
                year_votes[y] += 1
        for q in qs:
            quarter_votes[q] += 2 if years else 1
            evidence.append(f"{label!r}: Q{q}")
        if not qs and len(months) == 1:
            m = next(iter(months))
            q = _MONTH_TO_Q[m]
            quarter_votes[q] += 1
            evidence.append(f"{label!r}: month {m} → Q{q}")

    year, y_n = _vote(year_votes)
    quarter, q_n = _vote(quarter_votes)
    result = PeriodInference(
        year=int(year) if year is not None else None,
        quarter=int(quarter) if quarter is not None else None,
        source="filename",
        evidence=evidence[:20],
    )

    if result.ok:
        result.confidence = "medium" if y_n >= 2 and q_n >= 2 else "low"
        result.warnings.append(
            "Period taken from filenames — date columns were empty or unreadable"
        )
    elif result.year is not None and result.quarter is None:
        # Never guess a quarter (IMPL-20260929-05): leave it unresolved.
        result.confidence = "low"
        result.warnings.append(
            f"Year {result.year} from filename but no quarter named"
        )
    else:
        result.warnings.append("Could not infer year or quarter from filenames either")
        result.confidence = "none"
        result.source = "none"
    return result


def _labels_for(paths: Sequence[Path], root: Optional[Path] = None) -> List[str]:
    out: List[str] = []
    for p in paths:
        out.append(p.name)
        if root is not None:
            try:
                out.append(str(p.relative_to(root)))
            except ValueError:
                pass
        for part in p.parts:
            if part not in {".", "..", p.name}:
                out.append(part)
    return out


def infer_period(
    raw_dir: Path,
    *,
    filenames: Optional[Sequence[str]] = None,
) -> PeriodInference:
    """Banners (high) win; a clearly different file name beats weaker content
    (``filename_override``); file names alone when content says nothing.
    Unresolved year or quarter is returned as not ``ok`` — never guessed."""
    raw_dir = Path(raw_dir)
    excel_paths = _iter_excel_paths(raw_dir)
    named = filename_period(excel_paths, filenames=filenames)
    content = infer_period_from_workbook_content(excel_paths, named=named)
    if content.combined:
        return content
    if content.ok and content.confidence == "high":
        return content
    if content.ok:  # medium / low: a clearly different file name wins
        return _apply_filename(content, named)

    all_paths: List[Path] = list(excel_paths)
    if raw_dir.is_file():
        all_paths = [raw_dir]
    elif raw_dir.is_dir():
        all_paths = [
            p for p in raw_dir.rglob("*")
            if p.is_file() and not p.name.startswith("~$") and not p.name.startswith(".")
        ]
    labels = list(filenames or [])
    labels.extend(_labels_for(all_paths, root=raw_dir if raw_dir.is_dir() else raw_dir.parent))
    if raw_dir.is_dir():
        labels.extend(raw_dir.parts[-3:])
    fallback = infer_period_from_labels(labels)

    # Content that is ok returned above (high as-is; medium/low through the
    # file-name check). From here content is not ok: file names decide, and
    # a year without a quarter stays unresolved.
    if content.ok and not fallback.ok:
        return content
    if fallback.ok and not content.ok:
        fallback.evidence = content.evidence + fallback.evidence
        return fallback
    if content.ok and fallback.ok:
        mixed = PeriodInference(
            year=content.year or fallback.year,
            quarter=content.quarter or fallback.quarter,
            confidence="medium",
            source="mixed",
            evidence=content.evidence + fallback.evidence,
            warnings=content.warnings + fallback.warnings,
        )
        return mixed
    out = content if content.evidence else fallback
    out.warnings = list(dict.fromkeys(content.warnings + fallback.warnings))
    out.evidence = content.evidence + fallback.evidence
    return out


def filename_period(
    paths: Sequence[Path], *, filenames: Optional[Sequence[str]] = None,
) -> Tuple[Optional[int], Optional[int], List[str]]:
    """(year, quarter, evidence) that the file names name *clearly*.

    A quarter is clear when every file that names one (Q2 / 2ND QTR / QTR 3 /
    a single month) names the same one; likewise a year (file name, or a
    parent folder named exactly for a year). Anything else → None.
    """
    qs: set = set()
    ys: set = set()
    ev: List[str] = []
    labels = [(p.name, p.parent.name) for p in paths] + [(n, "") for n in (filenames or [])]
    for name, parent in labels:
        stem = Path(name).stem
        q = set(quarters_in_text(stem))
        if not q:
            months = {m for m in (token_month(t) for t in name_tokens(stem)) if m}
            mq = {_MONTH_TO_Q[m] for m in months}
            if len(mq) == 1:
                q = mq
        y = set(years_in_text(stem))
        if not y and re.fullmatch(r"(19|20)\d{2}", parent or ""):
            y = {int(parent)}
        if q:
            qs |= q
            ev.append(f"file name {name!r}: Q{'/'.join(map(str, sorted(q)))}")
        if y:
            ys |= y
    year = next(iter(ys)) if len(ys) == 1 else None
    quarter = next(iter(qs)) if len(qs) == 1 else None
    return year, quarter, ev


def _apply_filename(content: PeriodInference,
                    named: Tuple[Optional[int], Optional[int], List[str]]) -> PeriodInference:
    """Content guess below high confidence: a file name that clearly names a
    different quarter or year replaces that part (logged)."""
    ny, nq, nev = named
    y, q = content.year, content.quarter
    changed = []
    if nq is not None and nq != q:
        changed.append(f"Q{q}→Q{nq}")
        q = nq
    if ny is not None and ny != y:
        changed.append(f"{y}→{ny}")
        y = ny
    if not changed:
        return content
    out = PeriodInference(
        year=y, quarter=q, confidence="medium", source="filename",
        evidence=nev + content.evidence, warnings=list(content.warnings),
    )
    out.filename_override = (
        f"content guess {content.year} Q{content.quarter} ({content.confidence}) "
        f"replaced by file name: {', '.join(changed)} → {y} Q{q}"
    )
    return out
