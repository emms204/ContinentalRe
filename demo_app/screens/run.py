"""Run screen — upload, clean, confirm unknown classes, download."""
from __future__ import annotations

import json

import streamlit as st

from demo_app import store
from demo_app.notices import render_notices
from demo_app.coverage_ui import render_coverage
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
        suffix = 'demo adapter' if row.get('demo_only') else row['partner_id'][:8]
        label = f"{name}  ·  {suffix}"
        labels.append(label)
        by_label[label] = row

    default = next((i for i, lab in enumerate(labels) if "AIICO(ARK)" in lab), 0)
    partner_label = st.selectbox("Partner", labels, index=default)
    partner = by_label[partner_label]
    if partner.get('demo_only'):
        st.caption('Local demo partner from the cleaner registry. Class decisions apply only to this run.')
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
    # Only reset the review when the upload set changes. The uploader still
    # holds the files on later reruns (class radios, Apply and re-run); clearing
    # the review then removes the button before its click is handled.
    pending = [(f.name, f.getvalue()) for f in files] if files else []
    if pending and pending != list(st.session_state.get("demo_uploads") or []):
        st.session_state.demo_uploads = pending
        st.session_state.pop("demo_review", None)
        st.session_state.pop("demo_outcome", None)
        st.session_state.pop("demo_failed_coverage", None)

    st.caption(f"{len(_uploads())} file(s) ready · partner {partner['name']}")

    if st.button("Clean", type="primary"):
        _run(partner["partner_id"], int(year), quarter, bordereau_type, delivery, ignored=None)

    review = st.session_state.get("demo_review")
    if review:
        _review(review, partner["partner_id"], int(year), quarter, bordereau_type, delivery)

    outcome = st.session_state.get("demo_outcome")
    if outcome:
        _downloads(outcome)
    elif st.session_state.get("demo_failed_coverage"):
        render_coverage(st.session_state.demo_failed_coverage)


def _run(partner_id, year, quarter, bordereau_type, delivery, ignored, row_mappings=None) -> None:
    uploads = _uploads()
    if not uploads:
        st.error("Upload at least one workbook first.")
        return
    st.session_state.demo_outcome = None
    st.session_state.demo_review = None
    st.session_state.demo_failed_coverage = None
    status = st.status("Preparing cleaning run", expanded=True)
    meter = status.progress(0.0, text="Preparing uploaded files")

    def report_progress(message, fraction):
        status.update(label=message)
        meter.progress(fraction, text=message)

    try:
        outcome = clean(
            uploads, partner_id=partner_id, year=year, quarter=quarter,
            bordereau_type=bordereau_type, delivery=delivery, ignored_labels=ignored,
            on_progress=report_progress, row_mappings=row_mappings,
        )
    except NeedsReview as exc:
        meter.empty()
        status.update(label="Class mapping required · review the labels below", state="complete", expanded=False)
        st.session_state.demo_review = {
            "unresolved": exc.unresolved,
            "warnings": exc.warnings,
            "ignored": exc.ignored,
            "coverage": exc.coverage,
            "row_mappings": row_mappings or [],
        }
        st.session_state.demo_outcome = None
        st.warning("Some class labels are not registered. Choose keep or ignore, then apply.")
        return
    except CleanError as exc:
        meter.empty()
        status.update(label="Cleaning stopped · see the error below", state="error", expanded=False)
        st.session_state.demo_review = None
        st.session_state.demo_failed_coverage = exc.coverage
        st.error(str(exc))
        return
    except Exception as exc:
        meter.empty()
        status.update(label="Cleaning stopped · see the error below", state="error", expanded=False)
        st.session_state.demo_review = None
        st.error(f"{type(exc).__name__}: {exc}")
        return
    st.session_state.demo_review = None
    status.update(label="Cleaning complete", state="complete", expanded=False)
    st.session_state.demo_outcome = outcome
    if outcome.saved_paths:
        st.session_state.last_output_path = str(outcome.saved_paths[0])
        st.session_state.last_outputs = [{"output_path": str(p)} for p in outcome.saved_paths]
        st.session_state.last_year = outcome.year
        st.session_state.last_quarter = outcome.quarter
        st.session_state.selected_output_path = str(outcome.saved_paths[0])
    st.success("Cleaning finished. Check the results below before downloading.")


def _render_corrections(warnings) -> None:
    corrections = []
    for warning in warnings or []:
        if warning.get('code') == 'heirs_correction_record':
            try:
                item = json.loads(warning['detail'])
                if item not in corrections:
                    corrections.append(item)
            except (ValueError, TypeError):
                pass
    if corrections:
        st.subheader("Correction ledger · review separately")
        st.warning("These signed recovery adjustments are excluded from paid claims. Review them before applying them to the reporting period.")
        display = [{"Source row": r.get('source_row'),
                    "Entry": 'Correct cession' if r.get('role') == 'correct_cession' else 'Prior cession reversal',
                    "Class": r.get('class'), "Signed recovery adjustment": r.get('recovery_adjustment'),
                    "Insured": r.get('insured'), "Claim": r.get('claim_no'), "Policy": r.get('policy_no'),
                    "File": r.get('file'), "Sheet": r.get('sheet')}
                   for r in corrections]
        st.dataframe(display, hide_index=True, use_container_width=True)


def _review(review, partner_id, year, quarter, bordereau_type, delivery) -> None:
    render_coverage(review.get("coverage"), pending=True)
    from demo_app.engine import record_class_options
    record_names = record_class_options(partner_id)
    options = approved_class_options(partner_id)
    names = [o["class_name"] for o in options]
    alias_names = {o['class_name'] for o in options if o.get('store_class')}
    if names:
        record_names = [n for n in record_names if n in names]
    _render_corrections(review.get("warnings"))
    st.subheader("Confirm classes")
    st.caption("Class choices come from the cleaner registry. Reviews without a saved class entry and source conflicts apply only to the selected records. Ignore excludes a group for this run.")
    decisions = []
    record_decisions = []
    missing_record_choices = False
    for i, entry in enumerate(review.get("unresolved") or []):
        label = entry.get("label") or ""
        source_conflict = entry.get('suggestion_reason') == 'conflicting_source_classes' or any(
            r.get('resolution', {}).get('rule') == 'conflicting_source_classes' for r in entry.get('rows', []))
        with st.container(border=True):
            st.markdown("#### Conflicting source classes" if source_conflict else f"#### {label}")
            st.caption(f"File: {entry.get('file') or '—'}")
            st.caption(f"Sheet: {entry.get('sheet') or '—'} · {entry.get('records', 0)} affected records")
            if entry.get('expected_class') and not entry.get('allows_record_class_override'):
                st.info(f"This partner accepts {entry['expected_class']}. Other classes remain recognized source evidence, "
                        "but are outside this partner's scope. Confirm the permitted class only if appropriate; "
                        "otherwise exclude the records or use the correct partner.")
            evidence_rows = entry.get('rows') or []
            yoa_evidence = [(r.get('resolution') or {}) for r in evidence_rows]
            if (source_conflict and yoa_evidence
                    and all(e.get('expected_class') == 'Terrorism & PVT' for e in yoa_evidence)
                    and len({(e.get('section_label'), e.get('subclass_label'), tuple(e.get('candidates') or []))
                             for e in yoa_evidence}) == 1):
                evidence = yoa_evidence[0]
                import hashlib
                scope = hashlib.sha256('|'.join(sorted(r['row_id'] for r in evidence_rows)).encode()).hexdigest()
                st.caption(f"Source account group / parent: {evidence.get('section_label')} → {evidence.get('section_class')}")
                st.caption(f"Source subclass: {evidence.get('subclass_label')} → {evidence.get('product_class')}")
                st.info('YOA defaults to Terrorism & PVT. The source fields disagree. Confirm a class for these records; '
                        'this decision applies only to their source record IDs and saves no alias.')
                action = st.radio('Action for these source records', ['keep', 'ignore'],
                    format_func=lambda value: 'Choose a class' if value == 'keep' else 'Exclude these records',
                    horizontal=True, key=f'yoa_action_{scope}')
                if action == 'ignore':
                    decisions.append((entry, 'ignore', ''))
                    continue
                candidates = [c for c in evidence.get('candidates', []) if c in record_names]
                choices = ['Select class…'] + candidates + [n for n in record_names if n not in candidates]
                selected = st.selectbox(f"Class for these {len(evidence_rows)} source records", choices,
                    key=f'yoa_class_{scope}')
                st.caption('Applies only to the listed records in this file and sheet. No Fire-to-Terrorism alias is created.')
                with st.expander('Affected source records'):
                    st.dataframe([{'Source row': r['source_row'], 'Policy': r.get('policy_no'),
                                   'Insured': r.get('insured')} for r in evidence_rows], hide_index=True)
                if selected in record_names:
                    record_decisions.append(dict(row_ids=[r['row_id'] for r in evidence_rows], class_name=selected,
                                                 file=entry.get('file'), sheet=entry.get('sheet')))
                else:
                    missing_record_choices = True
                continue
            if source_conflict:
                st.info('The section heading and row class disagree. Choose a class for each source record. These decisions apply only to these records; no alias is saved.')
                action = st.radio('Action for conflicting records', ['keep','ignore'], horizontal=True,
                    format_func=lambda value:'Review these records' if value=='keep' else 'Exclude these records',
                    key=f'conflict_action_{i}')
                if action == 'ignore':
                    decisions.append((entry,'ignore',''))
                    continue
                for record in entry.get('rows', []):
                    with st.container(border=True):
                        evidence = record.get('resolution') or {}
                        st.markdown(f"**Row {record['source_row']} · {record.get('insured', '')}**")
                        st.caption(f"Policy: {record.get('policy_no') or '—'}")
                        st.write(f"Section says {evidence.get('section_class', 'unknown')}; row says {evidence.get('row_class') or record.get('raw_class') or 'unknown'}")
                        st.caption(f"Source heading: {evidence.get('section_label') or '—'}")
                        if evidence.get('subclass_label'):
                            st.caption(f"Source subclass heading: {evidence['subclass_label']}")
                        proposed = evidence.get('product_class')
                        if proposed in record_names:
                            st.caption(f"Record-cover suggestion: {proposed}. The source headings disagree; confirm the class for this record.")
                        candidates = [c for c in evidence.get('candidates', []) if c in record_names]
                        choices = ['Select class…'] + candidates + [n for n in record_names if n not in candidates]
                        selected = st.selectbox(f"Map source row {record['source_row']} to", choices,
                            key=f"conflict_class_{record['row_id']}")
                        if selected in record_names:
                            record_decisions.append(dict(row_ids=[record['row_id']], class_name=selected,
                                file=entry.get('file'), sheet=entry.get('sheet')))
                        else:
                            missing_record_choices = True
                continue
            if is_bare_marine(entry) and entry.get('rows'):
                st.info('Marine contains Hull and Cargo risks. Review each source record; suggestions are advisory and do not create a MARINE alias.')
                action = st.radio('Marine group action', ['review records', 'ignore group'], key=f'marine_action_{i}', horizontal=True)
                if action == 'ignore group':
                    decisions.append((entry, 'ignore', ''))
                    continue
                choices = ['Select class…'] + [n for n in record_names if n in ('Marine Hull','Marine Cargo')]
                for record in entry['rows']:
                    st.caption(f"Row {record['source_row']} · Policy {record.get('policy_no','')} · Claim {record.get('claim_no','')} · {record.get('insured','')}")
                    st.write(record.get('description') or 'No loss description supplied.')
                    proposed = (record.get('suggestion') or {}).get('class')
                    st.caption(f'Suggestion: {proposed} — source loss subject; confirm policy cover.' if proposed else 'No reliable suggestion. Review source policy cover.')
                    selected = st.selectbox(f"Map MARINE row {record['source_row']} to", choices,
                        index=choices.index(proposed) if proposed in choices else 0, key=f"marine_{record['row_id']}_{proposed or 'none'}")
                    if selected in record_names:
                        record_decisions.append(dict(row_ids=[record['row_id']], class_name=selected, file=entry.get('file'), sheet=entry.get('sheet')))
                continue
            if (entry.get('requires_row_resolution') or not names) and entry.get('rows'):
                st.info('Choose a class for each source record. These choices apply only to this run; no alias is saved.')
                action = st.radio(f"Action for {label}", ['keep', 'ignore'],
                    format_func=lambda value: 'Map these records' if value == 'keep' else 'Exclude these records',
                    horizontal=True, key=f"record_action_{i}_{label}")
                if action == 'ignore':
                    decisions.append((entry, 'ignore', ''))
                    continue
                for record in entry['rows']:
                    st.caption(f"Row {record['source_row']} · {record.get('insured', '')} · Policy {record.get('policy_no') or '—'}")
                    if record.get('description'):
                        st.write(record['description'])
                    proposed = (record.get('suggestion') or {}).get('class') or (entry.get('suggestion') or {}).get('class')
                    choices = ['Select class…'] + record_names
                    if proposed in record_names:
                        st.caption(f"Suggestion: {proposed}. This choice applies only to this source record.")
                    selected = st.selectbox(f"Map {label} · row {record['source_row']} to",
                        choices, index=choices.index(proposed) if proposed in choices else 0,
                        key=f"record_class_{record['row_id']}")
                    if selected in record_names:
                        record_decisions.append(dict(row_ids=[record['row_id']], class_name=selected,
                            file=entry.get('file'), sheet=entry.get('sheet')))
                    else:
                        missing_record_choices = True
                continue
            suggestion = (entry.get("suggestion") or {}).get("class")
            if is_bare_marine(entry):
                st.info("Bare MARINE is not auto-suggested. Pick Marine Hull or Marine Cargo, or ignore.")
                suggestion = None
            elif suggestion:
                st.caption(f"Suggestion: {suggestion} ({entry.get('suggestion_reason') or (entry.get('suggestion') or {}).get('rule')})")
            else:
                st.caption("No reliable suggestion. Choose the parent class using the source records.")
            action = st.radio(
                f"Action for {label}", ["keep", "ignore"], key=f"act_{i}_{label}", horizontal=True,
                format_func=lambda value: "Map these records" if value == "keep" else "Exclude these records",
            )
            choices = ['Select class…'] + names
            chosen = st.selectbox(
                f"Map {label} to", choices,
                index=choices.index(suggestion) if suggestion in names else 0,
                key=f"cls_{i}_{label}", disabled=action != "keep" or not names,
            )
            if action == "keep":
                if chosen in alias_names:
                    st.caption(f"Applies to all {entry.get('records', 0)} {label} records in this sheet. The label mapping is saved locally for this partner.")
                else:
                    st.caption(f"Applies only to these {entry.get('records', 0)} source records for this run; no alias is saved.")
            else:
                st.caption(f"All {entry.get('records', 0)} {label} records in this sheet will be excluded from this run.")
            decisions.append((entry, action, chosen))
    if st.button("Apply and re-run", type="primary"):
        ignore = []
        try:
            if missing_record_choices:
                raise ValueError('Choose a class for each source record before applying.')
            if any(action == 'keep' and chosen not in names for entry,action,chosen in decisions):
                raise ValueError('Choose an approved class for each kept label before applying.')
            for entry, action, chosen in decisions:
                if action == "ignore":
                    ignore.append({
                        "label": entry.get("label") or "",
                        "file": entry.get("file") or "",
                        "sheet": entry.get("sheet") or "",
                    })
                else:
                    if chosen in alias_names:
                        keep_alias(partner_id, entry.get("label") or "", chosen, confirmed_by="bisola-demo")
                    elif entry.get('rows'):
                        record_decisions.append(dict(row_ids=[r['row_id'] for r in entry['rows']],
                            class_name=chosen,file=entry.get('file'),sheet=entry.get('sheet')))
                    else:
                        raise ValueError('Re-run Clean to load source record IDs before applying this class choice.')
        except ValueError as exc:
            st.error(str(exc))
            return
        # Keep prior source-bound decisions when another label still needs review.
        ignored_groups = {(e.get('file'),e.get('sheet')) for e,action,_ in decisions if action == 'ignore'}
        combined = {rid: item for item in review.get('row_mappings', [])
                    if (item.get('file'),item.get('sheet')) not in ignored_groups for rid in item['row_ids']}
        combined.update({rid:item for item in record_decisions for rid in item['row_ids']})
        # A group is indexed once per record above. Rebuild groups so each
        # record is sent exactly once, including when a prior group is split.
        grouped = {}
        for row_id, item in combined.items():
            key = (item['class_name'], item.get('file'), item.get('sheet'))
            if key not in grouped:
                grouped[key] = dict(item, row_ids=[])
            grouped[key]['row_ids'].append(row_id)
        _run(partner_id, year, quarter, bordereau_type, delivery, ignored=ignore or None,
             row_mappings=list(grouped.values()))
        st.rerun()
    render_notices(review.get("warnings"), key="review_notices")


def _downloads(outcome) -> None:
    _render_corrections(outcome.warnings)
    render_coverage(getattr(outcome, "coverage", None))
    # Keep the deliverables above diagnostic details, even for large batches.
    st.subheader("Cleaned workbooks")
    if outcome.ignored_files or outcome.ignored or any(
        w.get("code") == "claims_file_ignored" for w in outcome.warnings
    ):
        st.warning("Output has exclusions. Review the excluded files and rows below before using it.")
    for item in outcome.files:
        st.download_button(
            f"Download {item.name}",
            data=item.data,
            file_name=item.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if not item.name.lower().endswith(".zip") else "application/zip",
            key=f"dl_{item.name}",
        )
    render_notices(
        outcome.warnings, ignored_files=outcome.ignored_files,
        ignored=outcome.ignored, key="output_notices", exclusions_announced=True,
    )
