"""Run screen — multi-file upload as a batch (IMPL-20260929-06).

Each uploaded file keeps its own reporting period; files are grouped by
(cedant, broker, year, quarter) and each group is one independent clean
(own status and exceptions). Pending files (period not settled, or naming
another cedant) are listed with inputs — never dropped. After the jobs the
user picks a delivery: separate workbooks (default, upload-ready), a zip of
them, or one merged REVIEW workbook (not for upload).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

from cre_cleaner.adapters import list_adapters
from cre_cleaner.core.batch import (
    STATUS_CEDANT_MISMATCH, STATUS_CEDANT_UNCONFIRMED, STATUS_CEDANT_UNKNOWN,
    plan_batch, run_batch,
)

_CEDANT_PENDING = {STATUS_CEDANT_MISMATCH, STATUS_CEDANT_UNCONFIRMED, STATUS_CEDANT_UNKNOWN}
from cre_cleaner.io.delivery import deliver
from demo_app import paths

_DELIVERY_LABELS = {
    "separate": "Separate workbooks — one per quarter (upload-ready, default)",
    "zip": "Zip of those workbooks",
    "merged": "One merged workbook — REVIEW / convenience only, NOT for upload",
}
_MIME = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
}


def _run(state: Dict[str, Any]) -> None:
    """(Re)plan and run the whole batch from ``state`` (files + overrides)."""
    plan = plan_batch(
        [Path(p) for p in state["files"]],
        cedant=state["cedant"], broker=state["broker"],
        overrides=state.get("overrides") or {},
    )
    root = Path(state["run_root"])
    with st.status(f"Cleaning {len(state['files'])} files as a batch…", expanded=True) as status:
        status.write(
            f"{len(plan.groups)} quarter group(s); {len(plan.pending)} file(s) need your input"
        )
        br = run_batch(
            plan,
            template=paths.TEMPLATE_PATH, out_dir=paths.OUTPUT_DIR,
            work_dir=root / "groups", base_dir=paths.REPO_ROOT,
            on_group=lambda g, i, n: status.write(
                f"Group {i}/{n}: {g.label} — {', '.join(f.name for f in g.files)}"),
            **state["pipeline_kwargs"],
        )
        failed = sum(1 for g in br.groups if g.status == "failed")
        status.update(
            label=f"Batch finished — {len(br.groups) - failed}/{len(br.groups)} group(s) produced output",
            state="complete" if not failed else "error",
        )
    state["plan_files"] = [f.as_dict() for f in plan.files]
    state["groups"] = [g.as_dict() for g in br.groups]
    state["deliverables"] = br.deliverable_outputs()
    state["delivery_built"] = {}
    state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.session_state.batch = state
    _publish(state)


def _publish(state: Dict[str, Any]) -> None:
    """Expose the batch outputs to Review / Compare like a normal run."""
    from demo_app.screens.run import _save_last_run
    outs = state.get("deliverables") or []
    st.session_state.last_outputs = outs
    if not outs:
        st.session_state.last_run_ok = False
        return
    first = outs[0]
    st.session_state.last_output_path = first["output_path"]
    st.session_state.selected_output_path = first["output_path"]
    st.session_state.last_exceptions_path = first.get("exceptions_path")
    st.session_state.last_audit_path = first.get("source_audit_path")
    st.session_state.last_summary = first.get("summary")
    st.session_state.last_premium_rows = sum(int(o.get("premium_rows") or 0) for o in outs)
    st.session_state.last_claims_rows = sum(int(o.get("claims_rows") or 0) for o in outs)
    st.session_state.last_outstanding_rows = sum(int(o.get("outstanding_rows") or 0) for o in outs)
    st.session_state.last_cedant = first.get("cedant")
    st.session_state.last_broker = first.get("broker")
    st.session_state.last_year = first.get("year")
    st.session_state.last_quarter = first.get("quarter")
    st.session_state.last_upload_names = [Path(p).name for p in state["files"]]
    st.session_state.last_run_ok = True
    _save_last_run(
        cedant=str(first.get("cedant")), broker=str(first.get("broker")),
        year=int(first.get("year")), quarter=int(first.get("quarter")),
        use_sample=False, bordereau_type=state["pipeline_kwargs"].get("bordereau_type", "all"),
        output_path=first["output_path"], exceptions_path=first.get("exceptions_path"),
        source_audit_path=first.get("source_audit_path"), outputs=outs,
        upload_names=st.session_state.last_upload_names,
    )


def start_batch(
    *, files: List[Path], cedant: Optional[str], broker: Optional[str], run_root: Path,
    pipeline_kwargs: Dict[str, Any],
) -> None:
    state = {
        "id": run_root.name, "files": [str(p) for p in files],
        "cedant": str(cedant or "").strip(), "broker": str(broker or "").strip(),
        "run_root": str(run_root), "pipeline_kwargs": dict(pipeline_kwargs),
        "overrides": {},
    }
    _run(state)


def _download(label: str, path: Path, key: str) -> None:
    if not path.is_file():
        st.caption(f"{label}: not available")
        return
    st.download_button(
        label=label, data=path.read_bytes(), file_name=path.name,
        mime=_MIME.get(path.suffix.lower(), "application/octet-stream"), key=key,
    )


def _pending_inputs(state: Dict[str, Any], pending: List[Dict[str, Any]]) -> None:
    st.warning(
        f"{len(pending)} file(s) were NOT cleaned yet — set their period / cedant "
        "below and re-run. They are not dropped."
    )
    adapters = [f"{c} / {b}" for c, b, _v, _n in list_adapters()]
    bid = state["id"]
    new_ov: Dict[str, Dict[str, Any]] = {}
    for i, f in enumerate(pending):
        st.markdown(f"**{f['file']}** — `{f['status']}`: {f['detail']}")
        c1, c2, c3 = st.columns([1, 1, 2])
        prev = (state.get("overrides") or {}).get(f["file"], {})
        year = c1.number_input(
            "Year", min_value=1990, max_value=2100, step=1,
            value=int(prev.get("year") or f.get("year") or datetime.now().year),
            key=f"b_{bid}_y_{i}",
        )
        qopts = [1, 2, 3, 4]
        q0 = prev.get("quarter") or f.get("quarter") or 1
        quarter = c2.selectbox("Quarter", qopts, index=qopts.index(int(q0)),
                               format_func=lambda q: f"Q{q}", key=f"b_{bid}_q_{i}")
        ov: Dict[str, Any] = {"year": int(year), "quarter": int(quarter)}
        if f["status"] in _CEDANT_PENDING:
            cur = f"{f['cedant']} / {f['broker']}"
            if cur not in adapters and f.get("cedant"):
                cur = next((a for a in adapters if a.startswith(f"{f['cedant']} / ")), cur)
            pick = c3.selectbox(
                "Cedant / broker (confirm)" if f["status"] == STATUS_CEDANT_UNCONFIRMED
                else "Cedant / broker",
                adapters, index=adapters.index(cur) if cur in adapters else 0,
                key=f"b_{bid}_c_{i}")
            ced, brk = [x.strip() for x in pick.split(" / ", 1)]
            ov.update(cedant=ced, broker=brk)
        new_ov[f["file"]] = ov
    label = ("Confirm and clean the listed files"
             if any(f["status"] in _CEDANT_PENDING for f in pending)
             else "Clean the listed files with these settings")
    if st.button(label, key=f"b_{bid}_rerun"):
        state.setdefault("overrides", {}).update(new_ov)
        _run(state)
        st.rerun()


def render_batch() -> None:
    state = st.session_state.get("batch")
    if not state:
        return
    groups = state.get("groups") or []
    pending = [f for f in state.get("plan_files") or [] if f["status"] != "grouped"]
    ok = [g for g in groups if g["status"] != "failed"]
    st.success(
        f"Batch of {len(state['files'])} file(s): {len(groups)} quarter group(s), "
        f"{len(ok)} produced output"
        + (f", {len(pending)} waiting for input" if pending else "")
    )

    st.markdown("#### Files")
    st.dataframe(
        [{"File": f["file"], "Cedant / broker": f"{f['cedant']} / {f['broker']}",
          "Period": f"{f['year']} Q{f['quarter']}" if f.get("year") and f.get("quarter") else "—",
          "Period from": f["period_source"], "Status": f["status"]}
         for f in state.get("plan_files") or []],
        hide_index=True, width="stretch",
    )
    st.markdown("#### Cleaning jobs (one per quarter)")
    st.dataframe(
        [{"Group": g["group"], "Status": g["status"], "Files": ", ".join(g["files"]),
          "Premium": g["premium_rows"], "Paid": g["claims_rows"],
          "Outstanding": g["outstanding_rows"],
          "Workbooks": ", ".join(Path(o["output_path"]).name for o in g["outputs"]) or "—"}
         for g in groups],
        hide_index=True, width="stretch",
    )
    for g in groups:
        if g.get("warnings"):
            st.warning(f"**{g['group']}** warnings:\n\n"
                       + "\n".join(f"- {w}" for w in g["warnings"][:8]))
        if g["errors"]:
            box = st.error if g["status"] == "failed" else st.warning
            box(f"**{g['group']}** ({g['status']}):\n\n" + "\n".join(f"- {e}" for e in g["errors"][:8]))

    if pending:
        _pending_inputs(state, pending)

    outs = state.get("deliverables") or []
    if not outs:
        st.info("No group produced an upload-ready workbook yet.")
        return
    st.markdown("#### Delivery")
    option = st.radio(
        "How do you want the cleaned output?", list(_DELIVERY_LABELS),
        format_func=_DELIVERY_LABELS.get, index=0, key=f"b_{state['id']}_delivery",
    )
    bid = state["id"]
    if option == "separate":
        st.caption("These per-quarter workbooks are the upload-ready files.")
        for i, o in enumerate(outs):
            p = Path(o["output_path"])
            st.write(f"**{o.get('group')}** · {o.get('currency') or ''} · "
                     f"premium {o.get('premium_rows')}, paid {o.get('claims_rows')}, "
                     f"outstanding {o.get('outstanding_rows')}")
            _download(f"Download {p.name}", p, f"b_{bid}_sep_{i}")
        return
    built = state.setdefault("delivery_built", {})
    if option not in built or not Path(built[option]).is_file():
        try:
            built[option] = str(deliver(outs, option, Path(state["run_root"]) / "delivery",
                                        stem=f"BATCH_{bid}")[0])
        except Exception as exc:
            st.error(f"Could not build the {option} delivery: {exc}")
            return
    p = Path(built[option])
    if option == "merged":
        st.warning("The merged workbook is for review only — upload the per-quarter "
                   "workbooks, not this file.")
    else:
        st.caption(f"Zip holds exactly the {len(outs)} per-quarter workbook(s).")
    _download(f"Download {p.name}", p, f"b_{bid}_{option}")
