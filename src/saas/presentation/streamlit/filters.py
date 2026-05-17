"""Sidebar filter widgets for the Smart Rental dashboard.

Returns a Filters dataclass that the view functions consume.
All helper queries that populate the widgets are cached at module import time
(they change only when the catalog changes — much less often than prices).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import streamlit as st

from .queries import (
    get_active_provider_codes,
    get_acriss_codes_with_data,
    get_pickup_date_range,
)


@dataclass
class Filters:
    pickup_date: date
    duration_days: int
    providers: tuple[str, ...]
    acriss_categories: tuple[str, ...] | None  # None = all
    include_pending_review: bool


_DURATION_OPTIONS = [1, 2, 3, 4, 5, 6, 7, 14, 21, 28]
_DEFAULT_DURATION_INDEX = 6  # 7 días


def render_sidebar_filters() -> Filters:
    with st.sidebar:
        st.header("Filtros")

        # ── Fecha de pickup ───────────────────────────────────────────────
        min_date, max_date = get_pickup_date_range()
        default_pickup = min(max_date, date.today() + timedelta(days=30))
        pickup_date = st.date_input(
            "Fecha de pickup",
            value=default_pickup,
            min_value=min_date,
            max_value=max_date,
        )

        # ── Duración ──────────────────────────────────────────────────────
        duration_days = st.selectbox(
            "Duración (días)",
            options=_DURATION_OPTIONS,
            index=_DEFAULT_DURATION_INDEX,
        )

        # ── Providers ─────────────────────────────────────────────────────
        all_providers = get_active_provider_codes()
        selected_providers = st.multiselect(
            "Providers",
            options=all_providers,
            default=all_providers,
        )

        # ── Categorías ACRISS ─────────────────────────────────────────────
        all_codes = get_acriss_codes_with_data()
        selected_codes = st.multiselect(
            "Categorías ACRISS",
            options=all_codes,
            default=[],
            help="Vacío = todas las categorías.",
        )

        # ── Pending review ────────────────────────────────────────────────
        include_pending = st.checkbox(
            "Incluir grupos en revisión 🔍",
            value=True,
            help=(
                "Los grupos 'en revisión' tienen una clasificación ACRISS tentativa "
                "que aún no ha sido confirmada por un operador."
            ),
        )

    return Filters(
        pickup_date=pickup_date,
        duration_days=int(duration_days),
        providers=tuple(selected_providers),
        acriss_categories=tuple(selected_codes) if selected_codes else None,
        include_pending_review=include_pending,
    )
