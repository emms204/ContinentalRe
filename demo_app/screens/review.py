"""Review screen — what's in the cleaned pack."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from demo_app import paths
from demo_app.workbook_metrics import preview_sheet, sheet_names


def _resolve_ours_path() -> Path | None:
    last = st.session_state.get("last_output_path")
    if last and Path(last).exists():
        return Path(last)
    if paths.FALLBACK_CLEANED.exists():
        return paths.FALLBACK_CLEANED
    return None


def render() -> None:
    st.subheader("What's in the pack")
    st.caption(
        "Upload workbook excludes audit logs; those are sidecars from the Run screen."
    )

    ours_path = _resolve_ours_path()
    if ours_path is None:
        st.warning("Run a clean first, or keep the saved Q2 sample on disk.")
        return

    st.code(str(ours_path), language=None)

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
