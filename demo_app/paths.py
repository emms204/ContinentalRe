"""Fixed paths for the Continental Re demo app."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cre_cleaner.paths import (
    DATA_GOLD,
    OUTPUT_DIR,
    REPO_ROOT,
    SAMPLE_RAW_DIR,
    TEMPLATE_PATH,
)

# Re-export for screens that import demo_app.paths
GOLD_ROOT = DATA_GOLD
DEMO_RUNS_DIR = OUTPUT_DIR / "demo_runs"
LAST_UPLOAD_ZIP = DEMO_RUNS_DIR / "last_upload.zip"  # legacy zip cache
LAST_UPLOAD_FILE = DEMO_RUNS_DIR / "last_upload.xlsx"  # legacy single-file cache
LAST_UPLOAD_NAME = DEMO_RUNS_DIR / "last_upload_name.txt"
LAST_UPLOAD_DIR = DEMO_RUNS_DIR / "last_upload"  # multi-file Excel cache
LAST_UPLOAD_MANIFEST = DEMO_RUNS_DIR / "last_upload_manifest.json"
LAST_FORM_JSON = DEMO_RUNS_DIR / "last_form.json"
LAST_RUN_JSON = DEMO_RUNS_DIR / "last_run.json"
_EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}

DEFAULT_CEDANT = "AIICO"
DEFAULT_BROKER = "ARK"
DEFAULT_YEAR = 2025
DEFAULT_QUARTER = 2

FALLBACK_CLEANED = OUTPUT_DIR / "AIICO_ARK_2025_Q2_cleaned.xlsx"

# AIICO_ARK_2020_Q2_cleaned.xlsx  (or without _cleaned)
_CLEANED_RE = re.compile(
    r"^(?P<cedant>[A-Za-z0-9]+)_(?P<broker>[A-Za-z0-9]+)_(?P<year>\d{4})_Q(?P<quarter>[1-4])",
    re.IGNORECASE,
)


def cleaned_name(cedant: str, broker: str, year: int, quarter: int) -> str:
    return f"{cedant.upper()}_{broker.upper()}_{year}_Q{quarter}_cleaned.xlsx"


def _gold_folder(cedant: str, broker: str) -> str:
    """gold/AIICO_ARK, gold/ROYAL_EXCHANGE_DIRECT, … — spaces → underscores."""
    c = cedant.upper().strip().replace(" ", "_")
    b = broker.upper().strip().replace(" ", "_")
    return f"{c}_{b}"


def gold_dir(cedant: str = DEFAULT_CEDANT, broker: str = DEFAULT_BROKER) -> Path:
    return GOLD_ROOT / _gold_folder(cedant, broker)


def gold_path(
    year: int,
    quarter: int,
    *,
    cedant: str = DEFAULT_CEDANT,
    broker: str = DEFAULT_BROKER,
) -> Path:
    """Canonical Bisola gold: data/gold/{CEDANT}_{BROKER}/{year}/Q{n}_{year}_BORDEREAU_NEW.xlsx.

    Falls back to ``.xlsm``, then currency-split ``_DOMESTIC`` / ``_FOREIGN``
    variants when Bisola only supplied those.
    """
    folder = gold_dir(cedant, broker) / str(year)
    stem = f"Q{quarter}_{year}_BORDEREAU_NEW"
    for suffix in (".xlsx", ".xlsm", ".xls"):
        candidate = folder / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    for tag in ("DOMESTIC", "FOREIGN"):
        for suffix in (".xlsx", ".xlsm", ".xls"):
            candidate = folder / f"{stem}_{tag}{suffix}"
            if candidate.is_file():
                return candidate
    return folder / f"{stem}.xlsx"


def default_gold_path() -> Path:
    return gold_path(DEFAULT_YEAR, DEFAULT_QUARTER)


def parse_cleaned_meta(
    cleaned: Optional[Path],
) -> Optional[Tuple[str, str, int, int]]:
    """Return (cedant, broker, year, quarter) from a cleaned workbook path."""
    if cleaned is None:
        return None
    stem = Path(cleaned).stem
    if stem.endswith("_cleaned"):
        stem = stem[: -len("_cleaned")]
    m = _CLEANED_RE.match(stem)
    if not m:
        return None
    return (
        m.group("cedant").upper(),
        m.group("broker").upper(),
        int(m.group("year")),
        int(m.group("quarter")),
    )


def resolve_gold_for_cleaned(cleaned: Optional[Path]) -> Path:
    """Gold path matching the cleaned file's year/quarter — never a different quarter."""
    meta = parse_cleaned_meta(cleaned)
    if meta is not None:
        cedant, broker, year, quarter = meta
        return gold_path(year, quarter, cedant=cedant, broker=broker)
    return default_gold_path()


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _unique_upload_name(dest_dir: Path, original_name: str) -> str:
    base = Path(original_name).name.strip() or "upload.xlsx"
    if not (dest_dir / base).exists():
        return base
    stem, suf = Path(base).stem, Path(base).suffix
    i = 2
    while (dest_dir / f"{stem}_{i}{suf}").exists():
        i += 1
    return f"{stem}_{i}{suf}"


def clear_upload_cache() -> None:
    """Remove multi-file and legacy single-file upload caches."""
    if LAST_UPLOAD_DIR.exists():
        shutil.rmtree(LAST_UPLOAD_DIR, ignore_errors=True)
    for p in (LAST_UPLOAD_MANIFEST, LAST_UPLOAD_FILE, LAST_UPLOAD_NAME, LAST_UPLOAD_ZIP):
        if p.exists():
            p.unlink()


def save_upload_cache(files: List[Tuple[bytes, str]]) -> List[str]:
    """Persist one or more Excel uploads. Returns saved basenames."""
    clear_upload_cache()
    if not files:
        return []
    LAST_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    names: List[str] = []
    for data, original_name in files:
        name = _unique_upload_name(LAST_UPLOAD_DIR, original_name)
        (LAST_UPLOAD_DIR / name).write_bytes(data)
        names.append(name)
    LAST_UPLOAD_MANIFEST.write_text(
        json.dumps({"files": names}, indent=2), encoding="utf-8"
    )
    # Keep legacy single-file pointers when only one upload (older code paths).
    if len(names) == 1:
        ONLY = LAST_UPLOAD_DIR / names[0]
        LAST_UPLOAD_FILE.write_bytes(ONLY.read_bytes())
        LAST_UPLOAD_NAME.write_text(names[0], encoding="utf-8")
    return names


def cached_upload_files() -> List[Path]:
    """Excel files in the upload cache (multi-file dir, else legacy single file)."""
    if LAST_UPLOAD_DIR.is_dir():
        files = sorted(
            p for p in LAST_UPLOAD_DIR.iterdir()
            if p.is_file()
            and p.suffix.lower() in _EXCEL_SUFFIXES
            and not p.name.startswith("~$")
        )
        if files:
            return files
    if LAST_UPLOAD_FILE.is_file():
        return [LAST_UPLOAD_FILE]
    return []


def cached_upload_names() -> List[str]:
    files = cached_upload_files()
    if not files:
        return []
    if LAST_UPLOAD_MANIFEST.exists():
        data = _load_json(LAST_UPLOAD_MANIFEST)
        listed = data.get("files") if isinstance(data.get("files"), list) else None
        if listed:
            by_name = {p.name: p for p in files}
            ordered = [by_name[n] for n in listed if n in by_name]
            rest = [p for p in files if p.name not in set(listed)]
            return [p.name for p in ordered + rest]
    if len(files) == 1 and LAST_UPLOAD_NAME.exists():
        name = LAST_UPLOAD_NAME.read_text(encoding="utf-8").strip()
        if name:
            return [name]
    return [p.name for p in files]


def copy_cached_uploads_to(raw_dir: Path) -> List[Path]:
    """Copy cached uploads into a run raw folder. Returns destination paths."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    dests: List[Path] = []
    names = cached_upload_names()
    files = {p.name: p for p in cached_upload_files()}
    for name in names:
        src = files.get(name)
        if src is None:
            continue
        dest_name = _unique_upload_name(raw_dir, name)
        dest = raw_dir / dest_name
        shutil.copy2(src, dest)
        dests.append(dest)
    return dests


def load_json(path: Path) -> dict:
    return _load_json(path)


def load_last_run() -> dict:
    return _load_json(LAST_RUN_JSON)


def output_entries(
    *,
    session_outputs: Any = None,
    session_primary: Any = None,
) -> List[Dict[str, Any]]:
    """Cleaned workbook entries for Run / Review / Compare (currency splits included)."""
    entries: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _add(entry: dict) -> None:
        path = str(entry.get("output_path") or "").strip()
        if not path or path in seen:
            return
        p = Path(path)
        if not p.is_file():
            return
        seen.add(path)
        row = dict(entry)
        row["output_path"] = str(p)
        if not row.get("exceptions_path"):
            exc = p.with_name(p.stem + "_exceptions.xlsx")
            if exc.is_file():
                row["exceptions_path"] = str(exc)
        if not row.get("source_audit_path"):
            audit = p.with_name(p.stem + "_source_audit.xlsx")
            if audit.is_file():
                row["source_audit_path"] = str(audit)
        entries.append(row)

    has_session = isinstance(session_outputs, list) and bool(session_outputs)
    if has_session:
        for e in session_outputs:
            if isinstance(e, dict):
                _add(e)
        if session_primary:
            _add({"output_path": str(session_primary), "currency": None})
        return entries

    if session_primary:
        _add({"output_path": str(session_primary), "currency": None})
        if entries:
            return entries

    disk = load_last_run()
    disk_outs = disk.get("outputs")
    if isinstance(disk_outs, list):
        for e in disk_outs:
            if isinstance(e, dict):
                _add(e)
    if disk.get("output_path"):
        _add({
            "output_path": str(disk["output_path"]),
            "currency": disk.get("currency"),
            "exceptions_path": disk.get("exceptions_path"),
            "source_audit_path": disk.get("source_audit_path"),
        })

    return entries


def output_label(entry: dict) -> str:
    path = Path(str(entry.get("output_path") or ""))
    ccy = entry.get("currency")
    prem = entry.get("premium_rows")
    claims = entry.get("claims_rows")
    ost = entry.get("outstanding_rows")
    bits = [path.name or "cleaned.xlsx"]
    if ccy:
        bits.append(str(ccy))
    counts = []
    if prem is not None:
        counts.append(f"prem {prem}")
    if claims is not None:
        counts.append(f"paid {claims}")
    if ost is not None:
        counts.append(f"ost {ost}")
    if counts:
        bits.append("·".join(counts))
    return " — ".join(bits)
