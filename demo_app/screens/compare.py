"""Compare screen — prove cleaned output against Bisola gold."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from demo_app import paths
from demo_app.workbook_metrics import compare_metrics, metrics_for_compare


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


def _resolve_gold_path(ours_path: Path) -> Path:
    """Prefer last-run year/quarter from session; else parse cleaned filename."""
    year = st.session_state.get("last_year")
    quarter = st.session_state.get("last_quarter")
    cedant = st.session_state.get("last_cedant") or paths.DEFAULT_CEDANT
    broker = st.session_state.get("last_broker") or paths.DEFAULT_BROKER
    if year and quarter:
        return paths.gold_path(int(year), int(quarter), cedant=cedant, broker=broker)
    return paths.resolve_gold_for_cleaned(ours_path)


def _fmt_money(n: float) -> str:
    return f"{n:,.2f}"


def _sheet_name_gaps(ours, gold) -> tuple[list[str], list[str]]:
    """Sheet-name diffs only among bordereau kinds that ours actually produced.

    A premium-only clean must not surface gold CLAIMS/OUTSTANDING tabs as gaps.
    """
    from demo_app.workbook_metrics import _classify_sheet

    kinds: set[str] = set()
    if ours.premium.rows:
        kinds.add("premium")
    if ours.paid.rows:
        kinds.add("paid")
    if ours.outstanding.rows:
        kinds.add("outstanding")
    if not kinds:
        return [], []

    def filtered(names: list[str]) -> set[str]:
        out: set[str] = set()
        for sn in names:
            k = _classify_sheet(sn)
            if k in kinds:
                out.add(sn)
        return out

    o = filtered(ours.data_sheets)
    g = filtered(gold.data_sheets)
    return sorted(o - g), sorted(g - o)


def render() -> None:
    st.subheader("Prove it")
    st.caption(
        "Sheet-derived totals vs Bisola’s gold — not SUMMARY tab claims. "
        "Single-month cleans are matched as a subset inside the quarterly gold. "
        "When a clean writes multiple workbooks, pick which one to compare."
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
            key="compare_output_picker",
        )
        st.session_state.selected_output_path = selected
        ours_path = Path(selected)
    else:
        ours_path = _resolve_ours_path()

    if ours_path is None:
        st.warning("Run a clean first, or keep the saved Q2 sample on disk.")
        return

    gold_path = _resolve_gold_path(ours_path)
    uploaded_gold = st.file_uploader(
        "Optional: upload a different Bisola gold file",
        type=["xlsx", "xls"],
        key="gold_upload",
    )
    if uploaded_gold is not None:
        override = paths.DEMO_RUNS_DIR / "gold_override.xlsx"
        override.parent.mkdir(parents=True, exist_ok=True)
        override.write_bytes(uploaded_gold.read())
        gold_path = override

    coverage = (st.session_state.get("last_summary") or {}).get("coverage") or {}
    # Prefer per-output summary when comparing a currency split workbook.
    selected_entry = next(
        (e for e in entries if e.get("output_path") == str(ours_path)),
        None,
    )
    if selected_entry and isinstance(selected_entry.get("summary"), dict):
        coverage = selected_entry["summary"].get("coverage") or coverage

    year = st.session_state.get("last_year")
    quarter = st.session_state.get("last_quarter")
    upload_names = st.session_state.get("last_upload_names")
    if upload_names:
        st.caption(
            "Source upload(s): " + ", ".join(f"`{n}`" for n in upload_names)
        )
    if coverage.get("kind") == "single_month" and coverage.get("month_label"):
        st.caption(
            f"Comparing **{str(coverage['month_label']).title()} {year}** "
            f"(single file) → matching rows inside **Q{quarter} {year}** gold."
        )
    elif year and quarter:
        st.caption(f"Comparing **Q{quarter} {year}** cleaned → full-quarter gold.")
    else:
        meta = paths.parse_cleaned_meta(ours_path)
        if meta:
            _c, _b, y, q = meta
            st.caption(f"Comparing **Q{q} {y}** cleaned → gold.")

    if not gold_path.exists():
        st.error(
            f"Gold file not found for this clean:\n`{gold_path}`\n\n"
            f"Expected under `{paths.gold_dir()}/{{year}}/Q{{n}}_{{year}}_BORDEREAU_NEW.xlsx`."
        )
        return

    c_left, c_right = st.columns(2)
    with c_left:
        st.markdown("**Ours (cre_cleaner)**")
        st.code(str(ours_path), language=None)
    with c_right:
        st.markdown("**Bisola gold**")
        st.code(str(gold_path), language=None)

    with st.spinner("Computing sheet totals…"):
        ours, gold = metrics_for_compare(
            ours_path, gold_path, coverage=coverage, exclude_fac=True,
        )
        headline = compare_metrics(ours, gold)

    if getattr(gold, "scope_note", ""):
        st.info(gold.scope_note)

    rows = []
    all_match = True
    for label, row in headline.items():
        match = bool(row["match"])
        all_match = all_match and match
        status = "Match" if match else "Diff"
        rows.append(
            {
                "Metric": label,
                "Ours rows": row["ours_rows"],
                "Gold rows": row["gold_rows"],
                "Ours amount": _fmt_money(row["ours_amount"]),
                "Gold amount": _fmt_money(row["gold_amount"]),
                "Status": status,
            }
        )

    st.dataframe(rows, width="stretch", hide_index=True)

    if all_match:
        st.success("All three headline metrics match Bisola gold.")
    else:
        st.warning("One or more metrics differ — expand “What differs” below.")

    with st.expander("What differs", expanded=not all_match):
        if all_match:
            st.caption("No headline row or amount gaps for this pair.")
        else:
            bullets = []
            for label, row in headline.items():
                if row["match"]:
                    continue
                bits = []
                if not row["rows_match"]:
                    bits.append(
                        f"rows {row['ours_rows']:,} vs {row['gold_rows']:,} "
                        f"(Δ {row['ours_rows'] - row['gold_rows']:+,})"
                    )
                if not row["amount_match"]:
                    delta = row["ours_amount"] - row["gold_amount"]
                    bits.append(
                        f"amount {_fmt_money(row['ours_amount'])} vs "
                        f"{_fmt_money(row['gold_amount'])} (Δ {_fmt_money(delta)})"
                    )
                bullets.append(f"- **{label}**: " + "; ".join(bits))
            # Sheet-name inventory is only meaningful for kinds present in the clean.
            # Premium-only uploads must not list gold CLAIMS/OUTSTANDING tabs as gaps.
            only_ours, only_gold = _sheet_name_gaps(ours, gold)
            if only_ours:
                bullets.append(
                    "- Sheets only in ours: " + ", ".join(f"`{s}`" for s in only_ours)
                )
            if only_gold:
                bullets.append(
                    "- Sheets only in gold (same bordereau kinds): "
                    + ", ".join(f"`{s}`" for s in only_gold)
                )
            st.markdown("\n".join(bullets) if bullets else "_No detail available._")
            if coverage.get("kind") == "single_month":
                st.caption(
                    "Month scope: gold counts only rows whose policy / period / amount "
                    "fingerprint matches this clean. Claims/outstanding gold sheets are "
                    "ignored when this clean had no claims/OST. Unmatched premium rows "
                    "mean mapping differs from Bisola."
                )

    s1, s2 = st.columns(2)
    with s1:
        st.markdown("**Our data sheets**")
        for sn in ours.data_sheets:
            st.write(f"- {sn}")
    with s2:
        title = (
            "**Gold sheets with matching rows**"
            if getattr(gold, "scope_note", "")
            else "**Gold data sheets**"
        )
        st.markdown(title)
        for sn in gold.data_sheets:
            st.write(f"- {sn}")
        if getattr(gold, "scope_note", "") and not gold.data_sheets:
            st.caption("(no matching gold rows for this clean)")

    # Never ignore a sheet silently: list everything not counted above.
    ours_skipped = getattr(ours, "skipped_sheets", []) or []
    gold_skipped = getattr(gold, "skipped_sheets", []) or []
    if ours_skipped or gold_skipped:
        unrecognised = [s for s in ours_skipped + gold_skipped if "not recognised" in s]
        if unrecognised:
            st.warning(
                f"{len(unrecognised)} sheet(s) could not be classified and are NOT "
                "counted in the totals above — see “Skipped sheets”."
            )
        k1, k2 = st.columns(2)
        with k1:
            st.markdown("**Skipped sheets — ours**")
            for sn in ours_skipped or ["(none)"]:
                st.write(f"- {sn}")
        with k2:
            st.markdown("**Skipped sheets — gold**")
            for sn in gold_skipped or ["(none)"]:
                st.write(f"- {sn}")
