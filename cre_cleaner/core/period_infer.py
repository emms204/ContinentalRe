"""Infer calendar year and quarter from bordereau *content*, not filenames.

Primary signal: date values in columns whose headers look like loss / cover /
transaction dates. Filenames and folder names are a weak fallback only when
the sheets yield too few parseable dates.
"""
from __future__ import annotations

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
from cre_cleaner.core.normalize import normalize_header, parse_date

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


def _header_date_weight(header: str) -> float:
    h = normalize_header(header)
    if not h:
        return 0.0
    best = 0.0
    for needle, w in _DATE_COL_WEIGHTS:
        if needle == h or needle in h:
            best = max(best, w)
    return best


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
) -> Tuple[Counter, Counter, List[str], Optional[Tuple[int, int]]]:
    """Return (year_votes, quarter_votes, evidence, banner_period).

    ``banner_period`` is (year, quarter) when a title row names both — that is
    the reporting period and must beat historical Date-of-Loss votes.
    """
    year_votes: Counter = Counter()
    quarter_votes: Counter = Counter()
    evidence: List[str] = []
    banner_period: Optional[Tuple[int, int]] = None
    if not rows:
        return year_votes, quarter_votes, evidence, banner_period

    # Banner / title rows above the header often name the reporting quarter.
    for r in rows[:12]:
        text = " ".join(str(c) for c in r if c not in (None, ""))
        if not text.strip():
            continue
        qs = quarters_in_text(text)
        ys = years_in_text(text)
        if qs and ys:
            # One clear reporting label (e.g. "2024 CLAIMS BORDEREAU - Q2, 2024")
            y = max(ys) if len(ys) <= 2 else Counter(ys).most_common(1)[0][0]
            q = sorted(qs)[0] if len(qs) == 1 else Counter(qs).most_common(1)[0][0]
            banner_period = (int(y), int(q))
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

    if header_idx is None:
        return year_votes, quarter_votes, evidence, banner_period

    evidence.append(
        f"header row {header_idx + 1}: date cols "
        + ", ".join(f"{c + 1}(w={col_weights[c]})" for c in sorted(col_weights)[:8])
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
            if d is None:
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


def infer_period_from_workbook_content(paths: Sequence[Path]) -> PeriodInference:
    """Majority vote over date-column values across Excel workbooks.

    An in-sheet banner that names year + quarter (e.g. "Q2, 2024 CLAIMS
    BORDEREAU") is the reporting period and wins over row Date-of-Loss votes.
    """
    from cre_cleaner.io.excel import read_workbook_sheets

    year_votes: Counter = Counter()
    quarter_votes: Counter = Counter()
    evidence: List[str] = []
    banner_votes: Counter = Counter()
    files_ok = 0

    for path in paths:
        try:
            sheets = read_workbook_sheets(path)
        except Exception as e:
            evidence.append(f"{path.name}: unreadable ({e})")
            continue
        file_hits = 0
        for sn, rows in sheets.items():
            yv, qv, ev, banner = _collect_dates_from_sheet(rows)
            if banner is not None:
                banner_votes[banner] += 1
            if yv or qv or banner is not None:
                file_hits += 1
                year_votes.update(yv)
                quarter_votes.update(qv)
                for e in ev[:2]:
                    evidence.append(f"{path.name}/{sn}: {e}")
        if file_hits:
            files_ok += 1

    # Reporting banner beats date-column majority when sheets agree.
    if banner_votes:
        (by, bq), bn = banner_votes.most_common(1)[0]
        if bn >= 1:
            result = PeriodInference(
                year=int(by), quarter=int(bq),
                confidence="high", source="content",
                evidence=[
                    f"sheet banner reporting period: {by} Q{bq} ({bn} sheet(s))"
                ] + evidence[:20],
            )
            return result

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
        result.quarter = 1
        result.confidence = "low"
        result.warnings.append(
            f"Year {result.year} from filename but no quarter — defaulting to Q1"
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
    """Prefer workbook date columns; fall back to filenames only if needed."""
    raw_dir = Path(raw_dir)
    excel_paths = _iter_excel_paths(raw_dir)
    content = infer_period_from_workbook_content(excel_paths)
    if content.ok and content.confidence in {"high", "medium"}:
        return content

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

    if content.ok and not fallback.ok:
        return content
    if fallback.ok and not content.ok:
        fallback.evidence = content.evidence + fallback.evidence
        return fallback
    if content.ok and fallback.ok:
        # Content won on year or quarter partially — fill gaps from filename.
        mixed = PeriodInference(
            year=content.year or fallback.year,
            quarter=content.quarter or fallback.quarter,
            confidence="medium",
            source="mixed",
            evidence=content.evidence + fallback.evidence,
            warnings=content.warnings + fallback.warnings,
        )
        return mixed
    # Neither ok
    out = content if content.evidence else fallback
    out.warnings = list(dict.fromkeys(content.warnings + fallback.warnings))
    out.evidence = content.evidence + fallback.evidence
    return out
