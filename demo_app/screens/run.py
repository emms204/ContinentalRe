"""Run screen — Phase 1: one Excel file per clean."""
from __future__ import annotations

import json
import shutil
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
_BORDEREAU_MODES = ("Premium", "Claims", "Outstanding", "All")
_MODE_TO_TYPE = {
    "Premium": "premium",
    "Claims": "claims",
    "Outstanding": "outstanding",
    "All": "all",
}
_EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}

_LEGACY_WIDGET_KEYS = (
    "w_cedant",
    "w_broker",
    "w_year",
    "w_quarter",
    "w_use_sample",
)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _cached_upload_path() -> Path | None:
    if paths.LAST_UPLOAD_FILE.is_file():
        return paths.LAST_UPLOAD_FILE
    return None


def _cached_upload_name() -> str | None:
    if not _cached_upload_path():
        return None
    if paths.LAST_UPLOAD_NAME.exists():
        name = paths.LAST_UPLOAD_NAME.read_text(encoding="utf-8").strip()
        if name:
            return name
    return paths.LAST_UPLOAD_FILE.name


def _save_upload_cache(data: bytes, original_name: str) -> None:
    paths.DEMO_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    paths.LAST_UPLOAD_FILE.write_bytes(data)
    paths.LAST_UPLOAD_NAME.write_text(
        Path(original_name).name.strip() or "upload.xlsx", encoding="utf-8"
    )


def _clear_upload_cache() -> None:
    for p in (paths.LAST_UPLOAD_FILE, paths.LAST_UPLOAD_NAME):
        if p.exists():
            p.unlink()


def _save_last_run(
    *,
    cedant: str,
    broker: str,
    year: int,
    quarter: int,
    use_sample: bool,
    bordereau_type: str,
    output_path: str | None = None,
) -> None:
    paths.DEMO_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "cedant": cedant,
        "broker": broker,
        "year": int(year),
        "quarter": int(quarter),
        "use_sample": bool(use_sample),
        "bordereau_type": bordereau_type,
    }
    if output_path:
        payload["output_path"] = str(output_path)
    paths.LAST_RUN_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _save_form_disk(
    *,
    cedant: str,
    broker: str,
    use_sample: bool,
    bordereau_mode: str,
) -> None:
    paths.DEMO_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    paths.LAST_FORM_JSON.write_text(
        json.dumps(
            {
                "cedant": cedant,
                "broker": broker,
                "use_sample": bool(use_sample),
                "bordereau_mode": bordereau_mode,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _authoritative_selection() -> dict:
    disk_run = _load_json(paths.LAST_RUN_JSON)
    disk_form = _load_json(paths.LAST_FORM_JSON)
    cached = _cached_upload_name() is not None

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
    else:
        cedant = paths.DEFAULT_CEDANT
        broker = paths.DEFAULT_BROKER
        year = paths.DEFAULT_YEAR
        quarter = paths.DEFAULT_QUARTER

    if "use_sample" in disk_run:
        use_sample = bool(disk_run["use_sample"])
    elif st.session_state.get("last_run_ok") and cached:
        use_sample = False
    elif "use_sample" in disk_form:
        use_sample = bool(disk_form["use_sample"])
    else:
        use_sample = not cached

    if (int(year), int(quarter)) != (paths.DEFAULT_YEAR, paths.DEFAULT_QUARTER):
        use_sample = False

    mode = (
        st.session_state.get("form_bordereau_mode")
        or disk_form.get("bordereau_mode")
        or disk_run.get("bordereau_type")
        or "All"
    )
    if isinstance(mode, str) and mode.lower() in _MODE_TO_TYPE.values():
        inv = {v: k for k, v in _MODE_TO_TYPE.items()}
        mode = inv.get(mode.lower(), "All")
    if mode not in _BORDEREAU_MODES:
        mode = "All"

    return {
        "cedant": str(cedant).upper(),
        "broker": str(broker).upper(),
        "year": int(year),
        "quarter": int(quarter),
        "use_sample": bool(use_sample),
        "bordereau_mode": mode,
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
        "form_use_sample": _cached_upload_name() is None,
        "form_bordereau_mode": "All",
        "run_hydrate": True,
    }
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

    if out:
        cleaned = Path(out)
        if cleaned.is_file():
            exc = cleaned.with_name(cleaned.stem + "_exceptions.xlsx")
            audit = cleaned.with_name(cleaned.stem + "_source_audit.xlsx")
            if not st.session_state.get("last_exceptions_path") and exc.is_file():
                st.session_state.last_exceptions_path = str(exc)
            if not st.session_state.get("last_audit_path") and audit.is_file():
                st.session_state.last_audit_path = str(audit)


def hydrate_form() -> None:
    sel = _authoritative_selection()
    st.session_state.form_cedant = sel["cedant"]
    st.session_state.form_broker = sel["broker"]
    st.session_state.form_use_sample = sel["use_sample"]
    st.session_state.form_bordereau_mode = sel["bordereau_mode"]

    for wk in _LEGACY_WIDGET_KEYS:
        st.session_state.pop(wk, None)

    st.session_state.form_epoch = int(st.session_state.get("form_epoch") or 0) + 1
    epoch = st.session_state.form_epoch
    st.session_state[f"cedant_{epoch}"] = sel["cedant"]
    st.session_state[f"broker_{epoch}"] = sel["broker"]
    st.session_state[f"use_sample_{epoch}"] = sel["use_sample"]
    st.session_state[f"mode_{epoch}"] = sel["bordereau_mode"]

    _save_form_disk(
        cedant=sel["cedant"],
        broker=sel["broker"],
        use_sample=sel["use_sample"],
        bordereau_mode=sel["bordereau_mode"],
    )


def _should_hydrate() -> bool:
    return bool(st.session_state.get("run_hydrate", True))


def _download_button(label: str, path: Path | str | None, key: str) -> None:
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


def _show_field_completeness(summary: dict | None) -> None:
    fc = (summary or {}).get("field_completeness") or {}
    if not fc:
        return
    with st.expander("Mapped field completeness", expanded=True):
        if fc.get("premium_rows"):
            st.write(
                f"**Premium** ({fc['premium_rows']} rows): "
                f"policy no {fc.get('premium_policy_no_pct')}% · "
                f"gross {fc.get('premium_gross_pct')}% · "
                f"retention {fc.get('premium_retention_pct')}% · "
                f"treaty {fc.get('premium_treaty_pct')}%"
            )
        if fc.get("claims_rows"):
            st.write(
                f"**Claims** ({fc['claims_rows']} rows): "
                f"policy no {fc.get('claims_policy_no_pct')}% · "
                f"total {fc.get('claims_total_pct')}%"
            )
        if fc.get("outstanding_rows"):
            st.write(
                f"**Outstanding** ({fc['outstanding_rows']} rows): "
                f"policy no {fc.get('outstanding_policy_no_pct')}% · "
                f"total {fc.get('outstanding_total_pct')}%"
            )


def render() -> None:
    _ensure_session_defaults()
    for wk in _LEGACY_WIDGET_KEYS:
        st.session_state.pop(wk, None)

    if _should_hydrate():
        hydrate_form()
        st.session_state.run_hydrate = False

    if "form_epoch" not in st.session_state:
        st.session_state.form_epoch = 0
        epoch0 = 0
        st.session_state[f"cedant_{epoch0}"] = st.session_state.form_cedant
        st.session_state[f"broker_{epoch0}"] = st.session_state.form_broker
        st.session_state[f"use_sample_{epoch0}"] = st.session_state.form_use_sample
        st.session_state[f"mode_{epoch0}"] = st.session_state.form_bordereau_mode

    epoch = int(st.session_state.form_epoch)

    st.subheader("Clean a bordereau")
    st.caption(
        "Phase 1: one Excel file → quarterly cleaned workbook. "
        "Year and quarter come from date columns in the file."
    )

    c1, c2 = st.columns(2)
    with c1:
        cedant = st.selectbox("Cedant", options=CEDANTS, key=f"cedant_{epoch}")
    with c2:
        brokers = BROKERS_BY_CEDANT.get(cedant, ["ARK"])
        broker_key = f"broker_{epoch}"
        if st.session_state.get(broker_key) not in brokers:
            st.session_state[broker_key] = brokers[0]
        broker = st.selectbox("Broker", options=brokers, key=broker_key)

    mode_label = st.radio(
        "Bordereau type",
        options=_BORDEREAU_MODES,
        horizontal=True,
        key=f"mode_{epoch}",
        help="Extract only this side from the workbook (Outstanding is its own type).",
    )
    bordereau_type = _MODE_TO_TYPE[mode_label]

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
        help="Wifi-proof demo path. Uncheck to upload one Excel bordereau.",
    )

    st.session_state.form_cedant = cedant
    st.session_state.form_broker = broker
    st.session_state.form_use_sample = bool(use_sample)
    st.session_state.form_bordereau_mode = mode_label

    cached_name = _cached_upload_name()

    if not use_sample:
        st.caption("Accepted: one `.xlsx` / `.xls` / `.xlsm` file (PDF paused for Phase 1)")

        if cached_name and _cached_upload_path():
            size_mb = _cached_upload_path().stat().st_size / (1024 * 1024)
            left, right = st.columns([4, 1])
            with left:
                st.success(f"Using cached upload: **{cached_name}** ({size_mb:.1f} MB)")
            with right:
                if st.button("Clear", key="clear_upload_cache", help="Remove cached upload"):
                    _clear_upload_cache()
                    st.session_state.form_use_sample = True
                    st.session_state.form_epoch = int(
                        st.session_state.get("form_epoch") or 0
                    ) + 1
                    e = st.session_state.form_epoch
                    st.session_state[f"cedant_{e}"] = st.session_state.form_cedant
                    st.session_state[f"broker_{e}"] = st.session_state.form_broker
                    st.session_state[f"use_sample_{e}"] = True
                    st.session_state[f"mode_{e}"] = st.session_state.form_bordereau_mode
                    st.rerun()

        uploaded = st.file_uploader(
            "Upload Excel bordereau" if not cached_name else "Replace cached upload",
            type=["xlsx", "xls", "xlsm"],
            accept_multiple_files=False,
            key=f"raw_file_{epoch}",
        )
        if uploaded is not None:
            data = uploaded.getvalue()
            name = Path(uploaded.name or "upload.xlsx").name
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
            cached = _cached_upload_path()
            if cached is None:
                st.warning("Upload one Excel file, or enable the local sample.")
                return
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_dir = paths.DEMO_RUNS_DIR / stamp / "raw"
            run_dir.mkdir(parents=True, exist_ok=True)
            dest_name = _cached_upload_name() or cached.name
            dest = run_dir / Path(dest_name).name
            shutil.copy2(cached, dest)
            raw_dir = run_dir

        if not paths.TEMPLATE_PATH.exists():
            st.error(f"Template not found: {paths.TEMPLATE_PATH}")
            return

        paths.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        try:
            with st.status("Cleaning bordereau…", expanded=True) as status:
                status.write(f"Mode: {mode_label}")
                status.write("Inferring year/quarter from date columns…")
                status.write("Writing class sheets…")
                result = run_pipeline(
                    cedant=str(cedant).strip(),
                    broker=str(broker).strip(),
                    year=None,
                    quarter=None,
                    raw_dir=raw_dir,
                    template=paths.TEMPLATE_PATH,
                    out_dir=paths.OUTPUT_DIR,
                    base_dir=paths.REPO_ROOT,
                    collapsed=collapsed,
                    include_audit_sheets=include_audit,
                    include_fac=include_fac,
                    convert_pdfs=False,
                    bordereau_type=bordereau_type,
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

        summary = result.summary or {}
        resolved_year = summary.get("year")
        resolved_quarter = summary.get("quarter")
        try:
            resolved_year = int(resolved_year) if resolved_year is not None else None
            resolved_quarter = int(resolved_quarter) if resolved_quarter is not None else None
        except (TypeError, ValueError):
            resolved_year, resolved_quarter = None, None
        if resolved_year is None or resolved_quarter is None:
            st.error(
                "Could not resolve year/quarter from the upload. "
                "Ensure date columns (Date of Loss, Cover From, Transaction Date, …) "
                "are populated in the workbook."
            )
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
        st.session_state.last_year = int(resolved_year)
        st.session_state.last_quarter = int(resolved_quarter)
        st.session_state.last_run_ok = True

        st.session_state.form_cedant = str(cedant).strip()
        st.session_state.form_broker = str(broker).strip()
        st.session_state.form_use_sample = bool(use_sample)
        st.session_state.form_bordereau_mode = mode_label

        _save_form_disk(
            cedant=str(cedant).strip(),
            broker=str(broker).strip(),
            use_sample=bool(use_sample),
            bordereau_mode=mode_label,
        )
        _save_last_run(
            cedant=str(cedant).strip(),
            broker=str(broker).strip(),
            year=int(resolved_year),
            quarter=int(resolved_quarter),
            use_sample=bool(use_sample),
            bordereau_type=bordereau_type,
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
                "no_claims_files",
                "period_unresolved",
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
        shown_year = st.session_state.get("last_year")
        shown_quarter = st.session_state.get("last_quarter")

        st.success(
            f"Cleaned workbook ready — **{shown_year} Q{shown_quarter}** "
            f"(from date columns)"
            if shown_year and shown_quarter
            else "Cleaned workbook ready"
        )
        m1, m2, m3 = st.columns(3)
        m1.metric("Premium rows", f"{prem:,}" if prem is not None else "—")
        m2.metric("Paid claims", f"{paid:,}" if paid is not None else "—")
        m3.metric("Outstanding claims", f"{ost:,}" if ost is not None else "—")

        st.write(f"Output: `{st.session_state.last_output_path}`")
        ccy = (st.session_state.last_summary or {}).get("currency")
        if ccy:
            st.caption(f"Primary currency workbook: **{ccy}**")

        _show_field_completeness(st.session_state.last_summary)

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
                "Sample is AIICO · ARK · 2025 — hit **Clean**. "
                "Year/quarter are read from the file."
            )
        elif cached_name:
            st.info(f"Cached **{cached_name}** is ready — hit **Clean**.")
        else:
            st.info("Upload one Excel bordereau, or enable the local sample.")
