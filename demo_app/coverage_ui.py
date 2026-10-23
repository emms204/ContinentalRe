"""Availability of each output type, before users download their workbooks."""
import streamlit as st

_STATUS = {"cleaned": "Rows published", "partial": "Published · review needed",
           "not_detected": "Not detected", "empty": "Empty source", "no_rows": "No rows published",
           "not_requested": "Not requested", "failed": "Failed", "blocked": "Mapping required"}


def render_coverage(rows, *, pending=False):
    if not rows:
        return
    st.subheader("Class review required" if pending else "Cleaning results")
    conflicts = [r for r in rows if any(d.get('code') == 'lasaco_outstanding_source_conflict'
                                       for d in r.get('details', []))]
    if conflicts and not pending:
        periods = ', '.join(dict.fromkeys(
            str(r.get('year') or '') + (f" Q{r['quarter']}" if r.get('quarter') else '')
            for r in conflicts))
        st.warning(f"Conflicting outstanding balances detected{(' for ' + periods) if periods else ''}. "
                   "Both source versions are included. Choose the controlling file and re-run with that version. "
                   "See Source and extraction details below for the competing amounts and source rows.")
    st.caption("Parsed rows are provisional; no batch download is available until class review is complete." if pending else "Counts are rows included in the downloadable workbooks. Claims means paid claims; outstanding is separate.")
    if not pending:
        columns = st.columns(3)
        for column, kind, label in zip(columns, ("premium","claims","outstanding"), ("Premium", "Claims (paid)", "Outstanding")):
            column.metric(label, sum(r["rows"] for r in rows if r["type"] == kind))
    table = []
    details = []
    for row in rows:
        period = str(row.get("year") or "") + (f" Q{row['quarter']}" if row.get("quarter") else "")
        table.append({"Period": period, "Type": row["label"], ("Rows ready (provisional)" if pending else "Published rows"): row["rows"],
                      "Status": ("Parsed · awaiting batch confirmation" if pending and row["status"] in {"cleaned", "partial"} else _STATUS[row["status"]]), "Reason": ("Parsing succeeded; awaiting class review before batch download." if pending and row["status"] in {"cleaned", "partial"} else row["reason"])})
        for item in row.get("sources", []):
            details.append({"Period":period, "Type":row["label"], "File":item["file"],
                            "Sheet":item["sheet"], "Evidence": "Source table",
                            "Detail":f"Rows read: {item.get('rows_read',0)}; parsed before output filters: {item.get('rows_parsed',0)}. {item.get('notes','')}"})
        for item in row.get("details", []):
            details.append({"Period":period, "Type":row["label"], "File":item["file"],
                            "Sheet":item["sheet"], "Evidence":item["code"], "Detail":item["detail"]})
    st.dataframe(table, hide_index=True, use_container_width=True)
    st.caption("“Not detected” means no table was identified for that type; it does not prove an unclassified source contains no such rows. Counts do not certify financial accuracy.")
    if details:
        with st.expander("Source and extraction details", expanded=False):
            st.dataframe(details, hide_index=True, use_container_width=True)
