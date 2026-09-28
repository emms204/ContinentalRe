"""Compare screen — prove cleaned output against Bisola gold."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from demo_app import paths
from demo_app.workbook_metrics import compare_metrics, compute_workbook_metrics


def _resolve_ours_path() -> Path | None:
    last = st.session_state.get("last_output_path")
    if last and Path(last).exists():
        return Path(last)
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


def render() -> None:
    st.subheader("Prove it")
    st.caption(
        "Sheet-derived totals vs Bisola’s gold — not SUMMARY tab claims."
    )

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

    meta = paths.parse_cleaned_meta(ours_path)
    if meta:
        _c, _b, year, quarter = meta
        st.caption(f"Comparing **Q{quarter} {year}** cleaned → gold.")

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
        ours = compute_workbook_metrics(ours_path, exclude_fac=True)
        gold = compute_workbook_metrics(gold_path, exclude_fac=True)
        headline = compare_metrics(ours, gold)

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
            only_ours = sorted(set(ours.data_sheets) - set(gold.data_sheets))
            only_gold = sorted(set(gold.data_sheets) - set(ours.data_sheets))
            if only_ours:
                bullets.append(
                    "- Sheets only in ours: " + ", ".join(f"`{s}`" for s in only_ours)
                )
            if only_gold:
                bullets.append(
                    "- Sheets only in gold: " + ", ".join(f"`{s}`" for s in only_gold)
                )
            st.markdown("\n".join(bullets) if bullets else "_No detail available._")

    s1, s2 = st.columns(2)
    with s1:
        st.markdown("**Our data sheets**")
        for sn in ours.data_sheets:
            st.write(f"- {sn}")
    with s2:
        st.markdown("**Gold data sheets**")
        for sn in gold.data_sheets:
            st.write(f"- {sn}")

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
