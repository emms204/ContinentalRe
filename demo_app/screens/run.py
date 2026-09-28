"""Run screen — clean a bordereau."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

import streamlit as st

from cre_cleaner.adapters import list_adapters
from cre_cleaner.adapters.base import UnsupportedCedantError
from cre_cleaner.pipeline import run_pipeline
from demo_app import paths

_ADAPTER_ROWS = list_adapters()
CEDANTS = sorted({c for c, _b, _v, _n in _ADAPTER_ROWS})
BROKERS_BY_CEDANT = {
    c: sorted({b for cc, b, _v, _n in _ADAPTER_ROWS if cc == c})
    for c in CEDANTS
}
YEARS = list(range(2020, 2027))
QUARTERS = [1, 2, 3, 4]

# Legacy keys from earlier attempts — purged so they cannot override the form.
_LEGACY_WIDGET_KEYS = (
    "w_cedant",
    "w_broker",
    "w_year",
    "w_quarter",
    "w_use_sample",
)


def _cached_upload_name() -> str | None:
    if not paths.LAST_UPLOAD_ZIP.exists():
        return None
    if paths.LAST_UPLOAD_NAME.exists():
        name = paths.LAST_UPLOAD_NAME.read_text(encoding="utf-8").strip()
        if name:
            return name
    return paths.LAST_UPLOAD_ZIP.name


def _save_upload_cache(data: bytes, original_name: str) -> None:
    paths.DEMO_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    paths.LAST_UPLOAD_ZIP.write_bytes(data)
    paths.LAST_UPLOAD_NAME.write_text(
        original_name.strip() or "upload.zip", encoding="utf-8"
    )


def _clear_upload_cache() -> None:
    for p in (paths.LAST_UPLOAD_ZIP, paths.LAST_UPLOAD_NAME):
        if p.exists():
            p.unlink()


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_last_run(
    *,
    cedant: str,
    broker: str,
    year: int,
    quarter: int,
    use_sample: bool,
    output_path: str | None = None,
) -> None:
    """Authoritative record of the last successful clean — not overwritten by form defaults."""
    paths.DEMO_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "cedant": cedant,
        "broker": broker,
        "year": int(year),
        "quarter": int(quarter),
        "use_sample": bool(use_sample),
    }
    if output_path:
        payload["output_path"] = str(output_path)
    paths.LAST_RUN_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _save_form_disk(
    *,
    cedant: str,
    broker: str,
    year: int,
    quarter: int,
    use_sample: bool,
) -> None:
    paths.DEMO_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    paths.LAST_FORM_JSON.write_text(
        json.dumps(
            {
                "cedant": cedant,
                "broker": broker,
                "year": int(year),
                "quarter": int(quarter),
                "use_sample": bool(use_sample),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _latest_extract_root() -> Path | None:
    """Most recent demo_runs/*/raw folder that still has Excel files."""
    if not paths.DEMO_RUNS_DIR.is_dir():
        return None
    candidates: list[Path] = []
    for child in paths.DEMO_RUNS_DIR.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        raw = child / "raw"
        if raw.is_dir():
            candidates.append(raw)
    for raw in sorted(candidates, key=lambda p: p.parent.name, reverse=True):
        if any(
            p.is_file() and p.suffix.lower() in {".xlsx", ".xls"}
            for p in raw.rglob("*")
            if not p.name.startswith("~$")
        ):
            return raw
    return None


def _ensure_upload_cache_from_extract() -> bool:
    """Rebuild last_upload.zip from the latest extract if the cache file is missing."""
    if paths.LAST_UPLOAD_ZIP.exists():
        return True
    raw = _latest_extract_root()
    if raw is None:
        return False

    # Prefer zipping a single top-level broker folder (e.g. raw/ARK → ARK.zip).
    children = [p for p in raw.iterdir() if not p.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir():
        root = children[0]
        arc_base = root.name
        name = f"{root.name}.zip"
    else:
        root = raw
        arc_base = "raw"
        name = "last_upload.zip"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in root.rglob("*"):
            if path.is_file() and not path.name.startswith("~$"):
                zf.write(path, arcname=str(Path(arc_base) / path.relative_to(root)))
    _save_upload_cache(buf.getvalue(), name)
    return True


def _authoritative_selection() -> dict:
    """Resolve cedant/broker/year/quarter/use_sample for the form.

    Priority matches what Compare shows: cleaned filename + last successful run,
    never the demo defaults when a clean already exists.
    """
    disk_run = _load_json(paths.LAST_RUN_JSON)
    disk_form = _load_json(paths.LAST_FORM_JSON)
    cached = _cached_upload_name() is not None
    if not cached:
        cached = _ensure_upload_cache_from_extract()

    out = st.session_state.get("last_output_path") or disk_run.get("output_path")
    meta = paths.parse_cleaned_meta(Path(out) if out else None)

    if meta is not None:
        cedant, broker, year, quarter = meta
    elif st.session_state.get("last_year") and st.session_state.get("last_quarter"):
        cedant = st.session_state.get("last_cedant") or paths.DEFAULT_CEDANT
        broker = st.session_state.get("last_broker") or paths.DEFAULT_BROKER
        year = int(st.session_state["last_year"])
        quarter = int(st.session_state["last_quarter"])
    elif disk_run.get("year") and disk_run.get("quarter"):
        cedant = disk_run.get("cedant") or paths.DEFAULT_CEDANT
        broker = disk_run.get("broker") or paths.DEFAULT_BROKER
        year = int(disk_run["year"])
        quarter = int(disk_run["quarter"])
    elif disk_form.get("year") and disk_form.get("quarter"):
        cedant = disk_form.get("cedant") or paths.DEFAULT_CEDANT
        broker = disk_form.get("broker") or paths.DEFAULT_BROKER
        year = int(disk_form["year"])
        quarter = int(disk_form["quarter"])
    else:
        cedant = paths.DEFAULT_CEDANT
        broker = paths.DEFAULT_BROKER
        year = paths.DEFAULT_YEAR
        quarter = paths.DEFAULT_QUARTER

    # Sample vs upload: prefer last successful clean, then cache presence.
    if "use_sample" in disk_run:
        use_sample = bool(disk_run["use_sample"])
    elif st.session_state.get("last_run_ok") and cached:
        use_sample = False
    elif "use_sample" in disk_form:
        use_sample = bool(disk_form["use_sample"])
    else:
        use_sample = not cached

    # Cleaned non-sample quarters always mean upload mode (sample is 2025 Q2 only).
    if (int(year), int(quarter)) != (paths.DEFAULT_YEAR, paths.DEFAULT_QUARTER):
        use_sample = False

    return {
        "cedant": str(cedant).upper(),
        "broker": str(broker).upper(),
        "year": int(year),
        "quarter": int(quarter),
        "use_sample": bool(use_sample),
    }


def _ensure_session_defaults() -> None:
    defaults = {
        "last_output_path": None,
        "last_exceptions_path": None,
        "last_audit_path": None,
        "last_outputs": None,
        "last_premium_rows": None,
        "last_claims_rows": None,
        "last_outstanding_rows": None,
        "last_summary": None,
        "last_run_ok": False,
        "last_cedant": None,
        "last_broker": None,
        "last_year": None,
        "last_quarter": None,
        "form_cedant": paths.DEFAULT_CEDANT,
        "form_broker": paths.DEFAULT_BROKER,
        "form_year": paths.DEFAULT_YEAR,
        "form_quarter": paths.DEFAULT_QUARTER,
        "form_use_sample": _cached_upload_name() is None,
        "run_hydrate": True,
    }
    # Restore last clean into session if this browser session is fresh but disk has it.
    disk_run = _load_json(paths.LAST_RUN_JSON)
    if disk_run.get("output_path") and defaults["last_output_path"] is None:
        out = Path(str(disk_run["output_path"]))
        if out.exists():
            defaults["last_output_path"] = str(out)
            defaults["last_run_ok"] = True
            defaults["last_cedant"] = disk_run.get("cedant")
            defaults["last_broker"] = disk_run.get("broker")
            defaults["last_year"] = disk_run.get("year")
            defaults["last_quarter"] = disk_run.get("quarter")

    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # Backfill year/quarter from the cleaned filename when session has output but
    # no last_year (e.g. hot-reload after that field was added).
    out = st.session_state.get("last_output_path")
    if out and not st.session_state.get("last_year"):
        meta = paths.parse_cleaned_meta(Path(out))
        if meta is not None:
            c, b, y, q = meta
            st.session_state.last_cedant = c
            st.session_state.last_broker = b
            st.session_state.last_year = y
            st.session_state.last_quarter = q
            st.session_state.last_run_ok = True

    # Sidecar paths are not stored in last_run.json — derive them from the cleaned file.
    if out:
        cleaned = Path(out)
        if cleaned.is_file():
            exc = cleaned.with_name(cleaned.stem + "_exceptions.xlsx")
            audit = cleaned.with_name(cleaned.stem + "_source_audit.xlsx")
            if not st.session_state.get("last_exceptions_path") and exc.is_file():
                st.session_state.last_exceptions_path = str(exc)
            if not st.session_state.get("last_audit_path") and audit.is_file():
                st.session_state.last_audit_path = str(audit)


def _stuck_on_demo_defaults() -> bool:
    """True when the form still shows the virgin demo prefill."""
    return (
        int(st.session_state.get("form_year") or 0) == paths.DEFAULT_YEAR
        and int(st.session_state.get("form_quarter") or 0) == paths.DEFAULT_QUARTER
        and bool(st.session_state.get("form_use_sample", True))
    )


def _should_hydrate() -> bool:
    if st.session_state.get("run_hydrate", True):
        return True
    sel = _authoritative_selection()
    # Poisoned session: defaults on screen, but a real clean exists.
    if _stuck_on_demo_defaults() and (
        sel["year"] != paths.DEFAULT_YEAR
        or sel["quarter"] != paths.DEFAULT_QUARTER
        or not sel["use_sample"]
    ):
        return True
    return False


def hydrate_form() -> None:
    """Bind form_* to the last clean and bump widget epoch so Streamlit redraws."""
    sel = _authoritative_selection()

    st.session_state.form_cedant = sel["cedant"]
    st.session_state.form_broker = sel["broker"]
    st.session_state.form_year = sel["year"]
    st.session_state.form_quarter = sel["quarter"]
    st.session_state.form_use_sample = sel["use_sample"]

    # Drop legacy keys that older builds kept alive across Compare.
    for wk in _LEGACY_WIDGET_KEYS:
        st.session_state.pop(wk, None)

    # New selectbox/checkbox identities → Streamlit must take our seeded values.
    st.session_state.form_epoch = int(st.session_state.get("form_epoch") or 0) + 1
    epoch = st.session_state.form_epoch
    st.session_state[f"cedant_{epoch}"] = sel["cedant"]
    st.session_state[f"broker_{epoch}"] = sel["broker"]
    st.session_state[f"year_{epoch}"] = sel["year"]
    st.session_state[f"quarter_{epoch}"] = sel["quarter"]
    st.session_state[f"use_sample_{epoch}"] = sel["use_sample"]

    _save_form_disk(
        cedant=sel["cedant"],
        broker=sel["broker"],
        year=sel["year"],
        quarter=sel["quarter"],
        use_sample=sel["use_sample"],
    )


def _has_excel(folder: Path) -> bool:
    return any(
        p.is_file() and p.suffix.lower() in {".xlsx", ".xls"}
        for p in folder.iterdir()
        if not p.name.startswith("~$") and not p.name.startswith(".")
    )


def _resolve_raw_dir(root: Path, year: int) -> Path:
    root = Path(root)
    year_token = str(year)

    if _has_excel(root):
        return root

    year_dir = root / year_token
    if year_dir.is_dir() and _has_excel(year_dir):
        return year_dir

    for candidate in sorted(root.rglob(year_token)):
        if candidate.is_dir() and _has_excel(candidate):
            return candidate

    children = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    if len(children) == 1 and _has_excel(children[0]):
        return children[0]

    return root


def _extract_zip_bytes(data: bytes, dest: Path, year: int) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    raw = dest / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(raw)

    children = [p for p in raw.iterdir() if not p.name.startswith(".")]
    start = children[0] if len(children) == 1 and children[0].is_dir() else raw
    return _resolve_raw_dir(start, year)


def _download_button(label: str, path: Path | str | None, key: str) -> None:
    # Path("") / Path(None) → "." which exists as a directory — reject that.
    if path is None or str(path).strip() in {"", "."}:
        st.caption(f"{label}: not available")
        return
    path = Path(path)
    if not path.is_file():
        st.caption(f"{label}: not available")
        return
    st.download_button(
        label=label,
        data=path.read_bytes(),
        file_name=path.name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=key,
    )


def render() -> None:
    _ensure_session_defaults()

    # Purge legacy widget keys every Run render (old interrupt-cleanup poison).
    for wk in _LEGACY_WIDGET_KEYS:
        st.session_state.pop(wk, None)

    if _should_hydrate():
        hydrate_form()
        st.session_state.run_hydrate = False

    if "form_epoch" not in st.session_state:
        st.session_state.form_epoch = 0
        # First paint with no hydrate path — seed epoch-0 keys from form_*.
        epoch0 = 0
        st.session_state[f"cedant_{epoch0}"] = st.session_state.form_cedant
        st.session_state[f"broker_{epoch0}"] = st.session_state.form_broker
        st.session_state[f"year_{epoch0}"] = st.session_state.form_year
        st.session_state[f"quarter_{epoch0}"] = st.session_state.form_quarter
        st.session_state[f"use_sample_{epoch0}"] = st.session_state.form_use_sample

    epoch = int(st.session_state.form_epoch)

    st.subheader("Clean a bordereau")
    st.caption(
        "Turn messy cedant Excel into a Continental-ready class-split workbook."
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        cedant = st.selectbox("Cedant", options=CEDANTS, key=f"cedant_{epoch}")
    with c2:
        brokers = BROKERS_BY_CEDANT.get(cedant, ["ARK"])
        broker_key = f"broker_{epoch}"
        if st.session_state.get(broker_key) not in brokers:
            st.session_state[broker_key] = brokers[0]
        broker = st.selectbox("Broker", options=brokers, key=broker_key)
    with c3:
        year = st.selectbox("Year", options=YEARS, key=f"year_{epoch}")
    with c4:
        quarter = st.selectbox("Quarter", options=QUARTERS, key=f"quarter_{epoch}")

    unverified = next(
        (note for c, b, verified, note in _ADAPTER_ROWS
         if c == cedant and b == broker and not verified),
        None,
    )
    if unverified:
        st.warning(f"First-pass adapter (unverified): {unverified}")

    use_sample = st.checkbox(
        "Use local sample (AIICO / ARK / 2025)",
        key=f"use_sample_{epoch}",
        help="Wifi-proof demo path. Uncheck to use a cached or newly uploaded zip.",
    )

    # Durable form_* mirrors the live widgets while the user edits.
    st.session_state.form_cedant = cedant
    st.session_state.form_broker = broker
    st.session_state.form_year = int(year)
    st.session_state.form_quarter = int(quarter)
    st.session_state.form_use_sample = bool(use_sample)

    cached_name = _cached_upload_name()
    if not use_sample and not cached_name:
        if _ensure_upload_cache_from_extract():
            cached_name = _cached_upload_name()

    if not use_sample:
        st.markdown(
            "**What to upload** — a zip of raw broker Excel for one year "
            "(monthly premium workbooks + the quarterly claims bordereau), "
            "or a single quarterly `.xlsx` / `.xls`. "
            f"Example: `AIICO/ARK.zip`; the cleaner uses the **{year}** folder inside it."
        )
        st.caption(
            "Expected inside the zip (or under `{broker}/{year}/`): files like "
            "`April Premium …xls`, `JANUARY PREM …xlsx`, "
            "`2nd Qtr … Claims Paid Bord.xlsx`, or one combined quarterly workbook."
        )

        if cached_name:
            size_mb = paths.LAST_UPLOAD_ZIP.stat().st_size / (1024 * 1024)
            left, right = st.columns([4, 1])
            with left:
                st.success(f"Using cached upload: **{cached_name}** ({size_mb:.1f} MB)")
            with right:
                if st.button("Clear", key="clear_upload_cache", help="Remove cached zip"):
                    _clear_upload_cache()
                    st.session_state.form_use_sample = True
                    st.session_state.form_epoch = int(
                        st.session_state.get("form_epoch") or 0
                    ) + 1
                    e = st.session_state.form_epoch
                    st.session_state[f"cedant_{e}"] = st.session_state.form_cedant
                    st.session_state[f"broker_{e}"] = st.session_state.form_broker
                    st.session_state[f"year_{e}"] = st.session_state.form_year
                    st.session_state[f"quarter_{e}"] = st.session_state.form_quarter
                    st.session_state[f"use_sample_{e}"] = True
                    st.rerun()

        uploaded = st.file_uploader(
            "Upload raw files (zip or Excel)" if not cached_name else "Replace cached upload",
            type=["zip", "xlsx", "xls"],
            help=(
                "Zip of raw monthly premium + quarterly claims Excel, or a single "
                "quarterly .xlsx/.xls. ARK.zip from AIICO/ is fine — pick Year to match."
            ),
            key=f"raw_zip_{epoch}",
        )
        if uploaded is not None:
            data = uploaded.getvalue()
            name = uploaded.name or "upload.zip"
            lower = name.lower()
            if lower.endswith((".xlsx", ".xls")):
                # Wrap a single workbook in a zip so the rest of the demo can
                # keep using last_upload.zip.
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr(Path(name).name, data)
                _save_upload_cache(buf.getvalue(), name)
                st.caption(f"Cached **{name}** (wrapped as zip) for later runs.")
                cached_name = name
            else:
                try:
                    with zipfile.ZipFile(io.BytesIO(data)) as zf:
                        zf.namelist()
                except zipfile.BadZipFile:
                    st.error("That file is not a valid zip.")
                else:
                    _save_upload_cache(data, name)
                    st.caption(f"Cached **{name}** for later runs.")
                    cached_name = name

    with st.expander("Advanced", expanded=False):
        st.caption(
            "Demo defaults match Continental guidance: Facultative ignored, "
            "audit logs as sidecars (not in the upload workbook)."
        )
        include_fac = st.checkbox("Include Facultative sheets", value=False)
        include_audit = st.checkbox("Embed audit sheets in workbook", value=False)
        collapsed = st.checkbox(
            "Collapsed single PREMIUM/CLAIMS/OUTSTANDING sheets", value=False
        )

    clean = st.button("Clean", type="primary", use_container_width=False)

    if clean:
        if use_sample:
            raw_dir = paths.SAMPLE_RAW_DIR
            if not raw_dir.is_dir():
                st.error(f"Sample raw folder not found: {raw_dir}")
                return
        else:
            if not paths.LAST_UPLOAD_ZIP.exists():
                if not _ensure_upload_cache_from_extract():
                    st.warning("Upload a zip of raw files, or enable the local sample.")
                    return
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_dir = paths.DEMO_RUNS_DIR / stamp
            try:
                raw_dir = _extract_zip_bytes(
                    paths.LAST_UPLOAD_ZIP.read_bytes(), run_dir, int(year)
                )
            except zipfile.BadZipFile:
                st.error("Cached file is not a valid zip. Clear it and upload again.")
                return
            if not _has_excel(raw_dir):
                st.error(
                    f"No Excel files found for {year} in that zip "
                    f"(looked under `{raw_dir}`). "
                    "Zip the year folder, or use ARK.zip and set Year correctly."
                )
                return

        if not paths.TEMPLATE_PATH.exists():
            st.error(f"Template not found: {paths.TEMPLATE_PATH}")
            return

        paths.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        try:
            with st.status("Cleaning bordereau…", expanded=True) as status:
                status.write("Reading premiums…")
                status.write("Merging quarter…")
                status.write("Writing class sheets…")
                result = run_pipeline(
                    cedant=str(cedant).strip(),
                    broker=str(broker).strip(),
                    year=int(year),
                    quarter=int(quarter),
                    raw_dir=raw_dir,
                    template=paths.TEMPLATE_PATH,
                    out_dir=paths.OUTPUT_DIR,
                    base_dir=paths.REPO_ROOT,
                    collapsed=collapsed,
                    include_audit_sheets=include_audit,
                    include_fac=include_fac,
                )
                status.update(label="Clean finished", state="complete")
        except UnsupportedCedantError as exc:
            st.error(str(exc))
            st.session_state.last_run_ok = False
            return
        except Exception as exc:
            st.error(f"Clean failed: {exc}")
            st.session_state.last_run_ok = False
            return

        st.session_state.last_output_path = result.output_path
        st.session_state.last_exceptions_path = result.exceptions_path
        st.session_state.last_audit_path = result.source_audit_path
        st.session_state.last_outputs = result.outputs
        st.session_state.last_premium_rows = len(result.premium_rows)
        st.session_state.last_claims_rows = len(result.claims_rows)
        st.session_state.last_outstanding_rows = len(result.outstanding_rows)
        st.session_state.last_summary = result.summary
        st.session_state.last_cedant = str(cedant).strip()
        st.session_state.last_broker = str(broker).strip()
        st.session_state.last_year = int(year)
        st.session_state.last_quarter = int(quarter)
        st.session_state.last_run_ok = True

        st.session_state.form_cedant = str(cedant).strip()
        st.session_state.form_broker = str(broker).strip()
        st.session_state.form_year = int(year)
        st.session_state.form_quarter = int(quarter)
        st.session_state.form_use_sample = bool(use_sample)

        _save_form_disk(
            cedant=str(cedant).strip(),
            broker=str(broker).strip(),
            year=int(year),
            quarter=int(quarter),
            use_sample=bool(use_sample),
        )
        _save_last_run(
            cedant=str(cedant).strip(),
            broker=str(broker).strip(),
            year=int(year),
            quarter=int(quarter),
            use_sample=bool(use_sample),
            output_path=str(result.output_path),
        )

        hard = [
            e
            for e in result.exceptions
            if getattr(e, "severity", "") == "ERROR"
            and getattr(e, "reason", "")
            in {
                "premium_file_unreadable",
                "claims_parse_failed",
                "no_premium_files",
            }
        ]
        if hard:
            st.warning(
                "Clean finished with source-file problems:\n\n"
                + "\n".join(
                    f"- **{e.source_filename or '(unknown)'}**: {e.detail}"
                    for e in hard
                )
            )

    if st.session_state.last_run_ok and st.session_state.last_output_path:
        prem = st.session_state.last_premium_rows
        paid = st.session_state.last_claims_rows
        ost = st.session_state.last_outstanding_rows

        st.success("Cleaned workbook ready")
        m1, m2, m3 = st.columns(3)
        m1.metric("Premium rows", f"{prem:,}" if prem is not None else "—")
        m2.metric("Paid claims", f"{paid:,}" if paid is not None else "—")
        m3.metric("Outstanding claims", f"{ost:,}" if ost is not None else "—")

        st.write(f"Output: `{st.session_state.last_output_path}`")
        ccy = (st.session_state.last_summary or {}).get("currency")
        if ccy:
            st.caption(f"Primary currency workbook: **{ccy}**")

        d1, d2, d3 = st.columns(3)
        with d1:
            _download_button(
                "Download cleaned.xlsx",
                Path(st.session_state.last_output_path),
                "dl_cleaned",
            )
        with d2:
            _download_button(
                "Exceptions sidecar",
                Path(st.session_state.last_exceptions_path or ""),
                "dl_exc",
            )
        with d3:
            _download_button(
                "Source audit sidecar",
                Path(st.session_state.last_audit_path or ""),
                "dl_audit",
            )

        extra = [
            e for e in (st.session_state.get("last_outputs") or [])
            if e.get("output_path") != st.session_state.last_output_path
        ]
        if extra:
            with st.expander(f"Other currency workbooks ({len(extra)})"):
                for i, e in enumerate(extra):
                    st.write(
                        f"**{e.get('currency')}** — premium {e.get('premium_rows')}, "
                        f"paid {e.get('claims_rows')}, outstanding {e.get('outstanding_rows')}"
                    )
                    _download_button(
                        f"Download {e.get('currency')} cleaned.xlsx",
                        Path(e.get("output_path") or ""),
                        f"dl_ccy_{i}",
                    )

        inv = (st.session_state.last_summary or {}).get("sheet_inventory") or {}
        if inv:
            with st.expander("Sheets written"):
                for sn, n in inv.items():
                    st.write(f"- **{sn}**: {n} rows")
    elif not clean:
        if use_sample:
            st.info(
                "Prefill is AIICO · ARK · 2025 · Q2 — hit **Clean** to produce the demo workbook."
            )
        elif cached_name:
            st.info(
                f"Cached **{cached_name}** is ready — pick year/quarter and hit **Clean**."
            )
        else:
            st.info("Upload a raw zip, or enable the local sample.")
