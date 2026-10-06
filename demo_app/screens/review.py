"""Review screen — what's in the cleaned pack."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from demo_app import paths
from demo_app.workbook_metrics import preview_sheet, sheet_names


def _resolve_entries() -> list[dict]:
    return paths.output_entries(
        session_outputs=st.session_state.get("last_outputs"),
        session_primary=st.session_state.get("last_output_path")
        or st.session_state.get("selected_output_path"),
    )


def _resolve_ours_path() -> Path | None:
    entries = _resolve_entries()
    if entries:
        selected = st.session_state.get("selected_output_path")
        for e in entries:
            if e.get("output_path") == selected:
                return Path(e["output_path"])
        return Path(entries[0]["output_path"])
    if paths.FALLBACK_CLEANED.exists():
        return paths.FALLBACK_CLEANED
    return None


def render() -> None:
    st.subheader("What's in the pack")
    st.caption(
        "Upload workbook excludes audit logs; those are sidecars from the Run screen. "
        "When a clean writes more than one workbook (e.g. currency split), pick which "
        "to inspect below."
    )

    entries = _resolve_entries()
    if len(entries) > 1:
        labels = {e["output_path"]: paths.output_label(e) for e in entries}
        options = [e["output_path"] for e in entries]
        current = st.session_state.get("selected_output_path")
        if current not in options:
            current = options[0]
        selected = st.selectbox(
            "Output workbook",
            options=options,
            index=options.index(current),
            format_func=lambda p: labels.get(p, p),
            key="review_output_picker",
        )
        st.session_state.selected_output_path = selected
        ours_path = Path(selected)
    else:
        ours_path = _resolve_ours_path()

    if ours_path is None:
        st.warning("Run a clean first, or keep the saved Q2 sample on disk.")
        return

    st.code(str(ours_path), language=None)
    upload_names = st.session_state.get("last_upload_names")
    if upload_names:
        st.caption(
            "Source upload(s): " + ", ".join(f"`{n}`" for n in upload_names)
        )

    names = sheet_names(ours_path)
    data_sheets = [
        n
        for n in names
        if n.upper().strip() not in {"SUMMARY", "SOURCE AUDIT", "EXCEPTIONS"}
    ]
    if not data_sheets:
        st.info("No class sheets found in this workbook.")
        return

    choice = st.selectbox("Sheet", options=data_sheets)
    headers, rows = preview_sheet(ours_path, choice, max_rows=20)
    if not headers:
        st.info("Could not detect a header row on this sheet.")
        return

    df = pd.DataFrame(rows, columns=headers)
    st.dataframe(df, width="stretch", hide_index=True)
    st.caption(f"Showing first {len(rows)} data rows.")
