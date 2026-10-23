"""Compact cleaning diagnostics; full source evidence stays available on demand."""
from __future__ import annotations

from collections import Counter
import csv
from io import StringIO

import streamlit as st

_LABELS = {
    "claims_file_ignored": "Claims files excluded",
    "unmapped_headers": "Unrecognised columns",
    "table_type_unknown": "Tables not loaded",
    "lasaco_outstanding_source_conflict": "Conflicting outstanding balances",
    "date_from_after_to": "Cover start is after cover end",
}
_FIELDS = ("Code", "File", "Sheet", "Row", "Detail")


def notice_rows(warnings):
    """Preserve every occurrence, including its source location, for export."""
    return [{
        "Code": str(w.get("code") or "warning"),
        "File": str(w.get("file") or ""),
        "Sheet": str(w.get("sheet") or ""),
        "Row": str(w.get("row") or ""),
        "Detail": str(w.get("detail") or w.get("message") or ""),
    } for w in warnings or []]


def grouped_rows(rows):
    counts = Counter(tuple(row[field] for field in _FIELDS) for row in rows)
    return [dict(zip(_FIELDS, values), Occurrences=count) for values, count in counts.items()]


def notices_csv(rows):
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=_FIELDS)
    writer.writeheader()
    for row in rows:
        # Diagnostics can contain workbook-controlled text. Keep the CSV safe
        # to open in spreadsheet software without interpreting it as formulas.
        writer.writerow({key: "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
                         for key, value in row.items()})
    return buffer.getvalue().encode("utf-8-sig")


def render_notices(warnings, *, ignored_files=None, ignored=None, key="notices", exclusions_announced=False):
    rows = notice_rows(warnings)
    ignored_files = list(ignored_files or [])
    ignored = list(ignored or [])
    claims = [row for row in rows if row["Code"] == "claims_file_ignored"]
    excluded_names = {str(item.get("file") or "") for item in ignored_files}
    excluded_names.update(row["File"] for row in claims)
    if ignored_files or claims:
        (st.caption if exclusions_announced else st.warning)(
            f"{len(excluded_names)} file(s) excluded from cleaning. "
            + (f"{len({row['File'] for row in claims})} include claims; output does not include those claims. " if claims else "")
            + "Check the selected year and quarter and review the details below."
        )
        with st.expander("Excluded files · reasons and source details", expanded=False):
            if ignored_files:
                st.dataframe([{
                    "File": item.get("file", ""), "Reason": item.get("reason", ""),
                    "Detail": item.get("detail", ""),
                } for item in ignored_files], hide_index=True, use_container_width=True)
            if claims:
                st.caption("Claims omitted from the output")
                st.dataframe(grouped_rows(claims), hide_index=True, use_container_width=True)
    if ignored:
        st.info(f"{len(ignored)} class selection(s) ignored for this run.")
        with st.expander("Ignored class rows", expanded=False):
            st.dataframe(ignored, hide_index=True, use_container_width=True)

    general = [row for row in rows if row["Code"] != "claims_file_ignored"]
    if general:
        counts = Counter(row["Code"] for row in general)
        st.caption(f"{len(general)} cleaning notice(s) across {len(counts)} categories. Repeated notices are grouped below.")
        with st.expander("Cleaning notices · summary and source details", expanded=False):
            st.dataframe([{
                "Category": _LABELS.get(code, code.replace("_", " ").capitalize()),
                "Occurrences": count,
            } for code, count in counts.items()], hide_index=True, use_container_width=True)
            st.caption("Full messages, filenames, sheets and rows (when supplied by the cleaner).")
            st.dataframe(grouped_rows(general), hide_index=True, use_container_width=True)
    if rows:
        with st.expander("Download full diagnostic report", expanded=False):
            st.caption("One row per original notice, including repeated occurrences.")
            st.download_button("Download diagnostic CSV", data=notices_csv(rows),
                               file_name="cleaning-notices.csv", mime="text/csv", key=f"{key}_csv")
