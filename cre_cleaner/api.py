"""Provisional FastAPI wrapper for cre_cleaner.

Upload a zip (or Excel files) → one cleaned zip per run, grouped as
cedant/broker/year/quarter/currency. Marked provisional until output grouping
is agreed with Tyrone.

Auth: set CRE_CLEANER_API_KEY and send it as header ``X-API-Key``.
Size: CRE_CLEANER_MAX_UPLOAD_MB (default 50). Uploads are never persisted under
``output/api_runs`` — work stays in a TemporaryDirectory deleted after the response.

Run:
  export CRE_CLEANER_API_KEY=…   # required for /clean and /clean/json
  cd ContinentalRe && uvicorn cre_cleaner.api:app --reload --port 8090
"""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from cre_cleaner.adapters import list_adapters
from cre_cleaner.adapters.base import UnsupportedCedantError
from cre_cleaner.pipeline import run_pipeline

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "TEMPLATE.xlsx"

_ALLOWED_UPLOAD_SUFFIXES = {".zip", ".xlsx", ".xls", ".xlsm"}
_LABEL_RE = re.compile(r"^[A-Z0-9][A-Z0-9 _.\-]{0,63}$")


def _max_upload_bytes() -> int:
    try:
        mb = int(os.environ.get("CRE_CLEANER_MAX_UPLOAD_MB", "50"))
    except ValueError:
        mb = 50
    return max(1, mb) * 1024 * 1024


def _configured_api_key() -> Optional[str]:
    key = os.environ.get("CRE_CLEANER_API_KEY", "").strip()
    return key or None


def require_api_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> None:
    expected = _configured_api_key()
    if not expected:
        raise HTTPException(
            503,
            "API key not configured; set CRE_CLEANER_API_KEY before accepting uploads",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(401, "Invalid or missing X-API-Key")


def sanitize_label(value: str, field: str) -> str:
    """Cedant / broker for paths and zip names — no path separators or traversal."""
    v = (value or "").strip().upper()
    if not v or not _LABEL_RE.fullmatch(v) or ".." in v:
        raise HTTPException(
            400,
            f"Invalid {field}: use up to 64 letters, digits, spaces, _ . - "
            f"(got {value!r})",
        )
    return v


def safe_upload_basename(filename: Optional[str], fallback_stem: str = "upload") -> str:
    """Keep only the final path component; reject traversal and odd names."""
    raw = (filename or "").replace("\\", "/")
    name = Path(raw).name  # drops directories including ../
    if not name or name in {".", ".."} or ".." in name:
        raise HTTPException(400, f"Unsafe filename: {filename!r}")
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(
            400,
            f"Unsupported upload type {suffix!r}; use .zip, .xlsx or .xls",
        )
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._-")[:80]
    if not stem:
        stem = fallback_stem
    return f"{stem}{suffix}"


async def read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                413,
                f"Upload exceeds size limit of {max_bytes} bytes "
                f"(set CRE_CLEANER_MAX_UPLOAD_MB to raise)",
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(400, "Empty upload")
    return data


app = FastAPI(
    title="cre_cleaner API (provisional)",
    description=(
        "Provisional Continental Re bordereau cleaner. "
        "Requires X-API-Key (CRE_CLEANER_API_KEY). "
        "Output grouping (cedant/broker/year/quarter/currency) is provisional "
        "until agreed with Tyrone."
    ),
    version="0.1.0-provisional",
)


@app.get("/health")
def health():
    return {
        "ok": True,
        "provisional": True,
        "auth_configured": _configured_api_key() is not None,
        "max_upload_mb": _max_upload_bytes() // (1024 * 1024),
    }


@app.get("/adapters")
def adapters():
    return [
        {
            "cedant": c,
            "broker": b,
            "verified": verified,
            "status_note": note,
        }
        for c, b, verified, note in list_adapters()
    ]


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts:
            raise HTTPException(400, f"Unsafe zip entry: {name}")
        target = (dest / name).resolve()
        try:
            target.relative_to(dest)
        except ValueError as e:
            raise HTTPException(400, f"Unsafe zip entry: {name}") from e
        if info.is_dir() or name.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _collect_inputs(raw_root: Path) -> Path:
    """Prefer a single top-level folder if the zip wraps one."""
    children = [p for p in raw_root.iterdir() if not p.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return raw_root


def _prepare_raw(data: bytes, basename: str, raw: Path) -> Path:
    suffix = Path(basename).suffix.lower()
    if suffix == ".zip":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                _safe_extract(zf, raw)
        except zipfile.BadZipFile as e:
            raise HTTPException(400, "Upload is not a valid zip") from e
        return _collect_inputs(raw)
    dest = raw / basename
    # Defend against any residual path components
    if dest.resolve().parent != raw.resolve():
        raise HTTPException(400, f"Unsafe filename: {basename!r}")
    dest.write_bytes(data)
    return raw


def _run_clean(
    *,
    cedant: str,
    broker: str,
    year: int,
    quarter: int,
    include_fac: bool,
    raw_dir: Path,
    out: Path,
):
    try:
        return run_pipeline(
            cedant=cedant,
            broker=broker,
            year=year,
            quarter=quarter,
            raw_dir=raw_dir,
            template=TEMPLATE,
            out_dir=out,
            base_dir=REPO_ROOT,
            include_fac=include_fac,
        )
    except UnsupportedCedantError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"Clean failed: {e}") from e


@app.post("/clean", dependencies=[Depends(require_api_key)])
async def clean(
    cedant: str = Form(...),
    broker: str = Form(...),
    year: int = Form(...),
    quarter: int = Form(..., ge=1, le=4),
    include_fac: bool = Form(False),
    file: UploadFile = File(..., description="Zip of raw Excel, or a single .xlsx/.xls"),
):
    """Clean one quarter. Returns a zip of cleaned workbooks + sidecars.

    Grouping inside the zip (provisional):
      {cedant}/{broker}/{year}/Q{quarter}/{currency}/
        {cedant}_{broker}_{year}_Q{quarter}_{currency}_cleaned.xlsx
        …_exceptions.xlsx
        …_source_audit.xlsx

    All work stays in a temporary directory deleted after the response.
    """
    if not TEMPLATE.exists():
        raise HTTPException(500, f"TEMPLATE.xlsx missing at {TEMPLATE}")

    cedant = sanitize_label(cedant, "cedant")
    broker = sanitize_label(broker, "broker")
    basename = safe_upload_basename(file.filename)
    data = await read_upload_limited(file, _max_upload_bytes())

    with tempfile.TemporaryDirectory(prefix="cre_api_") as tmp:
        tmp_path = Path(tmp)
        raw = tmp_path / "raw"
        out = tmp_path / "out"
        raw.mkdir()
        out.mkdir()

        raw_dir = _prepare_raw(data, basename, raw)
        result = _run_clean(
            cedant=cedant,
            broker=broker,
            year=year,
            quarter=quarter,
            include_fac=include_fac,
            raw_dir=raw_dir,
            out=out,
        )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            manifest = {
                "provisional": True,
                "grouping": "cedant/broker/year/quarter/currency",
                "cedant": cedant,
                "broker": broker,
                "year": year,
                "quarter": quarter,
                "outputs": [],
                "note": (
                    "Output grouping is provisional until agreed with Tyrone. "
                    "Adapters other than AIICO/ARK are first-pass / unverified."
                ),
            }
            for entry in result.outputs:
                ccy = entry["currency"]
                prefix = f"{cedant}/{broker}/{year}/Q{quarter}/{ccy}"
                for key in ("output_path", "exceptions_path", "source_audit_path"):
                    p = Path(entry[key])
                    if p.is_file():
                        zf.write(p, arcname=f"{prefix}/{p.name}")
                manifest["outputs"].append({
                    "currency": ccy,
                    "premium_rows": entry["premium_rows"],
                    "claims_rows": entry["claims_rows"],
                    "outstanding_rows": entry["outstanding_rows"],
                    "cleaned": f"{prefix}/{Path(entry['output_path']).name}",
                })
            zf.writestr(
                f"{cedant}/{broker}/{year}/Q{quarter}/manifest.json",
                json.dumps(manifest, indent=2),
            )

        buf.seek(0)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"{cedant}_{broker}_{year}_Q{quarter}_cleaned_{stamp}.zip"
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )


@app.post("/clean/json", dependencies=[Depends(require_api_key)])
async def clean_json(
    cedant: str = Form(...),
    broker: str = Form(...),
    year: int = Form(...),
    quarter: int = Form(..., ge=1, le=4),
    include_fac: bool = Form(False),
    file: UploadFile = File(...),
):
    """Same as /clean but returns JSON metrics.

    Files are written only under a TemporaryDirectory that is deleted when this
    handler returns — paths in the JSON are therefore not durable. Use /clean
    when you need the cleaned workbooks.
    """
    if not TEMPLATE.exists():
        raise HTTPException(500, f"TEMPLATE.xlsx missing at {TEMPLATE}")

    cedant = sanitize_label(cedant, "cedant")
    broker = sanitize_label(broker, "broker")
    basename = safe_upload_basename(file.filename)
    data = await read_upload_limited(file, _max_upload_bytes())

    with tempfile.TemporaryDirectory(prefix="cre_api_json_") as tmp:
        tmp_path = Path(tmp)
        raw = tmp_path / "raw"
        out = tmp_path / "out"
        raw.mkdir()
        out.mkdir()

        raw_dir = _prepare_raw(data, basename, raw)
        result = _run_clean(
            cedant=cedant,
            broker=broker,
            year=year,
            quarter=quarter,
            include_fac=include_fac,
            raw_dir=raw_dir,
            out=out,
        )

        # Summarise without leaking absolute temp paths that vanish after return.
        outputs = []
        for entry in result.outputs:
            outputs.append({
                "currency": entry["currency"],
                "premium_rows": entry["premium_rows"],
                "claims_rows": entry["claims_rows"],
                "outstanding_rows": entry["outstanding_rows"],
                "cleaned_name": Path(entry["output_path"]).name,
                "exceptions_name": Path(entry["exceptions_path"]).name,
                "source_audit_name": Path(entry["source_audit_path"]).name,
            })

        return JSONResponse({
            "provisional": True,
            "persisted": False,
            "cedant": cedant,
            "broker": broker,
            "year": year,
            "quarter": quarter,
            "outputs": outputs,
            "exception_count": len(result.exceptions),
            "adapter_status": (result.summary or {}).get("adapter_status"),
            "note": "Temp outputs deleted after response; download via /clean for files.",
        })
