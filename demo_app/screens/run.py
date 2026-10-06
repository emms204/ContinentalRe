"""Run screen — upload, clean, confirm unknown classes, download."""
from __future__ import annotations

import streamlit as st

from demo_app import store
from demo_app.engine import CleanError, NeedsReview, approved_class_options, clean, is_bare_marine, keep_alias

_TYPES = ("autodetect", "premium", "claims", "premium_claims")
_DELIVERY = ("separate", "merged", "zip")


def _uploads() -> list[tuple[str, bytes]]:
    return list(st.session_state.get("demo_uploads") or [])


def render() -> None:
    st.markdown(
        '<p class="cre-banner">Demo store — confirmations stay on this site only '
        "and are not synced to production.</p>",
        unsafe_allow_html=True,
    )
    try:
        rows = store.partners()
    except FileNotFoundError as exc:
        st.error(str(exc))
        return

    labels = []
    by_label = {}
    for row in rows:
        name = str(row.get("name") or "").strip() or row["partner_id"]
        label = f"{name}  ·  {row['partner_id'][:8]}"
        labels.append(label)
        by_label[label] = row

    default = next((i for i, lab in enumerate(labels) if "AIICO(ARK)" in lab), 0)
    partner_label = st.selectbox("Partner", labels, index=default)
    partner = by_label[partner_label]
    year = st.number_input("Year", min_value=2000, max_value=2100, value=2025, step=1)
    quarter_choice = st.selectbox(
        "Quarter",
        ["Year only (one workbook per quarter, zipped if several)", "Q1", "Q2", "Q3", "Q4"],
    )
    quarter = None if quarter_choice.startswith("Year only") else int(quarter_choice[1])
    bordereau_type = st.selectbox("Bordereau type", _TYPES, index=0)
    delivery = st.selectbox("Delivery", _DELIVERY, index=0)
    files = st.file_uploader(
        "Bordereaux (.xls / .xlsx)",
        type=["xls", "xlsx", "xlsm"],
        accept_multiple_files=True,
    )
    if files:
        st.session_state.demo_uploads = [(f.name, f.getvalue()) for f in files]
        st.session_state.pop("demo_review", None)

    st.caption(f"{len(_uploads())} file(s) ready · partner {partner['name']}")

    if st.button("Clean", type="primary"):
        _run(partner["partner_id"], int(year), quarter, bordereau_type, delivery, ignored=None)

    review = st.session_state.get("demo_review")
    if review:
        _review(review, partner["partner_id"], int(year), quarter, bordereau_type, delivery)

    outcome = st.session_state.get("demo_outcome")
    if outcome:
        _downloads(outcome)


def _run(partner_id, year, quarter, bordereau_type, delivery, ignored) -> None:
    uploads = _uploads()
    if not uploads:
        st.error("Upload at least one workbook first.")
        return
    try:
        outcome = clean(
            uploads, partner_id=partner_id, year=year, quarter=quarter,
            bordereau_type=bordereau_type, delivery=delivery, ignored_labels=ignored,
        )
    except NeedsReview as exc:
        st.session_state.demo_review = {
            "unresolved": exc.unresolved,
            "warnings": exc.warnings,
            "ignored": exc.ignored,
        }
        st.session_state.demo_outcome = None
        st.warning("Some class labels are not registered. Choose keep or ignore, then apply.")
        return
    except CleanError as exc:
        st.session_state.demo_review = None
        st.error(str(exc))
        return
    except Exception as exc:
        st.session_state.demo_review = None
        st.error(f"{type(exc).__name__}: {exc}")
        return
    st.session_state.demo_review = None
    st.session_state.demo_outcome = outcome
    if outcome.saved_paths:
        st.session_state.last_output_path = str(outcome.saved_paths[0])
        st.session_state.last_outputs = [{"output_path": str(p)} for p in outcome.saved_paths]
        st.session_state.last_year = outcome.year
        st.session_state.last_quarter = outcome.quarter
        st.session_state.selected_output_path = str(outcome.saved_paths[0])
    st.success("Cleaned. Download below.")


def _review(review, partner_id, year, quarter, bordereau_type, delivery) -> None:
    options = approved_class_options(partner_id)
    names = [o["class_name"] for o in options]
    st.subheader("Confirm classes")
    st.caption("Keep saves an alias on this site for the selected partner. Ignore drops those rows for this run only.")
    for w in review.get("warnings") or []:
        st.warning(f"{w.get('code')}: {w.get('detail')}")
    decisions = []
    for i, entry in enumerate(review.get("unresolved") or []):
        label = entry.get("label") or ""
        st.markdown(
            f"**{label}** — {entry.get('file') or '—'} / {entry.get('sheet') or '—'} · "
            f"{entry.get('records', 0)} records"
        )
        suggestion = (entry.get("suggestion") or {}).get("class")
        if is_bare_marine(entry):
            st.info("Bare MARINE is not auto-suggested. Pick Marine Hull or Marine Cargo, or ignore.")
            suggestion = None
        elif suggestion:
            st.caption(f"Suggestion: {suggestion} ({entry.get('suggestion_reason') or (entry.get('suggestion') or {}).get('rule')})")
        else:
            st.caption(f"No suggestion ({entry.get('suggestion_reason') or 'unresolved'}).")
        cols = st.columns([1, 2])
        action = cols[0].radio(
            "Action", ["keep", "ignore"], key=f"act_{i}_{label}", horizontal=True,
            label_visibility="collapsed",
        )
        default_idx = 0
        if suggestion and suggestion in names:
            default_idx = names.index(suggestion)
        elif "Marine Hull" in names:
            default_idx = names.index("Marine Hull")
        chosen = cols[1].selectbox(
            "Approved class", names or ["(no approved classes in snapshot)"],
            index=default_idx if names else 0,
            key=f"cls_{i}_{label}",
            disabled=action != "keep" or not names,
        )
        decisions.append((entry, action, chosen))
    if st.button("Apply and re-run", type="primary"):
        ignore = []
        try:
            for entry, action, chosen in decisions:
                if action == "ignore":
                    ignore.append({
                        "label": entry.get("label") or "",
                        "file": entry.get("file") or "",
                        "sheet": entry.get("sheet") or "",
                    })
                else:
                    keep_alias(
                        partner_id, entry.get("label") or "", chosen, confirmed_by="bisola-demo",
                    )
        except ValueError as exc:
            st.error(str(exc))
            return
        _run(partner_id, year, quarter, bordereau_type, delivery, ignored=ignore or None)
        st.rerun()


def _downloads(outcome) -> None:
    if outcome.ignored_files:
        st.warning("Ignored files: " + "; ".join(
            f"{i.get('file')} ({i.get('reason')})" for i in outcome.ignored_files
        ))
    if outcome.ignored:
        st.info("Ignored class rows: " + "; ".join(
            f"{i.get('label')} ({i.get('records', '?')} records)" for i in outcome.ignored
        ))
    for w in outcome.warnings:
        st.warning(f"{w.get('code')}: {w.get('detail')}")
    for item in outcome.files:
        st.download_button(
            f"Download {item.name}",
            data=item.data,
            file_name=item.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if not item.name.lower().endswith(".zip") else "application/zip",
            key=f"dl_{item.name}",
        )
