"""Continental Re · Bordereau Cleaner — Streamlit demo."""
from __future__ import annotations

import sys
from pathlib import Path

# Allow `streamlit run demo_app/app.py` from repo root
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import streamlit as st

from demo_app.screens import compare as compare_screen
from demo_app.screens import review as review_screen
from demo_app.screens import run as run_screen

st.set_page_config(
    page_title="Continental Re · Bordereau Cleaner",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="collapsed",
)

CSS = """
<style>
    :root {
        --cre-blue: #3d6eb5;
        --cre-bg: #1e2430;
        --cre-panel: #2a3140;
        --cre-card: #323a4b;
        --cre-text: #e6e9ef;
        --cre-muted: #9aa3b2;
        --cre-border: #3f4758;
    }
    .stApp, [data-testid="stAppViewContainer"],
    [data-testid="stHeader"], section.main {
        background: var(--cre-bg) !important;
        color: var(--cre-text);
    }
    [data-testid="stToolbar"], [data-testid="stDecoration"] {
        background: var(--cre-bg) !important;
    }
    .block-container {
        padding-top: 2.75rem !important;
    }
    h1.cre-heading {
        color: #f2f5fa !important;
        font-size: 1.75rem !important;
        font-weight: 700 !important;
        letter-spacing: -0.01em;
        margin: 0 0 0.25rem 0 !important;
        line-height: 1.25 !important;
        padding-top: 0.25rem;
    }
    .cre-sub {
        color: var(--cre-muted) !important;
        font-size: 0.95rem;
        margin-bottom: 1rem;
    }
    .cre-footer {
        margin-top: 2.5rem;
        padding-top: 1rem;
        border-top: 1px solid var(--cre-border);
        color: var(--cre-muted);
        font-size: 0.85rem;
    }
    div[data-testid="stMetric"] {
        background: var(--cre-card);
        border: 1px solid var(--cre-border);
        border-radius: 8px;
        padding: 0.75rem 1rem;
    }
    div[data-testid="stMetric"] label,
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        color: var(--cre-text) !important;
    }
    .stButton > button[kind="primary"] {
        background-color: var(--cre-blue);
        border-color: var(--cre-blue);
        color: #fff;
    }
    div[data-testid="stAlert"] {
        background: var(--cre-panel);
    }
    hr {
        border-color: var(--cre-border) !important;
    }
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)

st.markdown(
    '<h1 class="cre-heading">Continental Re · Bordereau Cleaner</h1>'
    '<p class="cre-sub">Deterministic Excel cleaning for quarterly bordereaux</p>',
    unsafe_allow_html=True,
)

nav = st.radio(
    "Navigation",
    options=["Run", "Compare", "Review"],
    horizontal=True,
    label_visibility="collapsed",
    key="nav_radio",
)

# Re-entering Run → re-bind the form to the last cleaned workbook.
prev_nav = st.session_state.get("nav_prev")
if prev_nav is not None and prev_nav != "Run" and nav == "Run":
    st.session_state.run_hydrate = True
st.session_state.nav_prev = nav

st.divider()

if nav == "Run":
    run_screen.render()
elif nav == "Compare":
    compare_screen.render()
else:
    review_screen.render()

st.markdown(
    '<div class="cre-footer">'
    "Deterministic cleaning · Bisola reviews exceptions · Continental upload-ready"
    "</div>",
    unsafe_allow_html=True,
)
