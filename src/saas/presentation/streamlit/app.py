"""Smart Rental — Streamlit dashboard entry point.

Run from the repo root:
    streamlit run src/saas/presentation/streamlit/app.py

Requires Postgres to be running and at least one completed scrape.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on the path regardless of the working directory.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

import streamlit as st

from src.saas.presentation.streamlit.filters import render_sidebar_filters
from src.saas.presentation.streamlit.views.overview import render_overview
from src.saas.presentation.streamlit.views.timeline import render_timeline
from src.saas.presentation.streamlit.views.tariff import render_tariff
from src.saas.presentation.streamlit.views.cross_tariff import render_cross_tariff

st.set_page_config(
    page_title="Smart Rental — Mercado",
    page_icon="🚗",
    layout="wide",
)

st.title("Smart Rental — Vista de Mercado")
st.caption("Comparativa de precios entre providers por categoría ACRISS")

filters = render_sidebar_filters()

tab_overview, tab_timeline, tab_tariff, tab_cross = st.tabs([
    "📊 Visión general",
    "📈 Evolución temporal",
    "📋 Tarifa por proveedor",
    "🎯 Tarifa cruzada",
])

with tab_overview:
    render_overview(filters)

with tab_timeline:
    render_timeline(filters)

with tab_tariff:
    render_tariff(filters)

with tab_cross:
    render_cross_tariff()
