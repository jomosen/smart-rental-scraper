"""Integration tests for Streamlit dashboard query functions.

Tests call the _*_impl functions directly (bypassing @st.cache_data) with the
super_engine from the conftest. All 3 queries must return a DataFrame with the
expected columns even when the DB has no matching data for the given filters.

Requires: Postgres running, .env set, alembic upgrade head applied.
Skip automatically when SUPER_DATABASE_URL is not set.
"""
from __future__ import annotations

import os
from datetime import date

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

_COLUMNS_OVERVIEW = {
    "acriss_code", "display_name", "provider_code", "provider_name",
    "min_price_per_day", "max_price_per_day", "avg_price_per_day",
    "pvc_count", "has_pending_review",
}
_COLUMNS_TIMELINE = {"pickup_date", "provider_code", "provider_name", "price_per_day"}
_COLUMNS_DETAIL = {
    "provider_code", "external_code", "example_models", "seats",
    "current_price_per_day", "pending_review", "classification_confidence",
}

_PICKUP_DATE = date(2026, 6, 15)
_DURATION = 7
_PROVIDERS = ("provider_a", "victoria_rent_a_car", "solcar")


def _require_engine():
    url = os.environ.get("SUPER_DATABASE_URL")
    if not url:
        pytest.skip("SUPER_DATABASE_URL not set — skipping dashboard query tests")
    return create_engine(url, future=True, pool_pre_ping=True)


@pytest.fixture(scope="module")
def engine():
    eng = _require_engine()
    yield eng
    eng.dispose()


class TestFetchMarketOverview:
    def test_returns_dataframe_with_expected_columns(self, engine):
        from src.saas.presentation.streamlit.queries import _fetch_market_overview_impl

        df = _fetch_market_overview_impl(
            engine,
            pickup_date=_PICKUP_DATE,
            duration_days=_DURATION,
            providers=_PROVIDERS,
            acriss_categories=None,
            include_pending_review=True,
        )

        assert set(df.columns) >= _COLUMNS_OVERVIEW, (
            f"Missing columns: {_COLUMNS_OVERVIEW - set(df.columns)}"
        )

    def test_empty_providers_returns_empty_df(self, engine):
        from src.saas.presentation.streamlit.queries import _fetch_market_overview_impl

        df = _fetch_market_overview_impl(
            engine,
            pickup_date=_PICKUP_DATE,
            duration_days=_DURATION,
            providers=(),
            acriss_categories=None,
            include_pending_review=True,
        )

        assert df.empty
        assert set(df.columns) >= _COLUMNS_OVERVIEW

    def test_acriss_filter_applied(self, engine):
        from src.saas.presentation.streamlit.queries import _fetch_market_overview_impl

        df_all = _fetch_market_overview_impl(
            engine,
            pickup_date=_PICKUP_DATE,
            duration_days=_DURATION,
            providers=_PROVIDERS,
            acriss_categories=None,
            include_pending_review=True,
        )
        df_filtered = _fetch_market_overview_impl(
            engine,
            pickup_date=_PICKUP_DATE,
            duration_days=_DURATION,
            providers=_PROVIDERS,
            acriss_categories=("IFAR",),
            include_pending_review=True,
        )

        # Filtered result can only contain IFAR rows (or be empty)
        if not df_filtered.empty:
            assert set(df_filtered["acriss_code"].unique()) == {"IFAR"}
        # Filtered can't have MORE rows than unfiltered
        assert len(df_filtered) <= len(df_all)

    def test_exclude_pending_review_reduces_or_equal_rows(self, engine):
        from src.saas.presentation.streamlit.queries import _fetch_market_overview_impl

        df_with = _fetch_market_overview_impl(
            engine,
            pickup_date=_PICKUP_DATE,
            duration_days=_DURATION,
            providers=_PROVIDERS,
            acriss_categories=None,
            include_pending_review=True,
        )
        df_without = _fetch_market_overview_impl(
            engine,
            pickup_date=_PICKUP_DATE,
            duration_days=_DURATION,
            providers=_PROVIDERS,
            acriss_categories=None,
            include_pending_review=False,
        )

        assert len(df_without) <= len(df_with)


class TestFetchTimeline:
    def test_returns_dataframe_with_expected_columns(self, engine):
        from src.saas.presentation.streamlit.queries import _fetch_timeline_impl

        df = _fetch_timeline_impl(
            engine,
            acriss_code="IFAR",
            duration_days=_DURATION,
            providers=_PROVIDERS,
            include_pending_review=True,
        )

        assert set(df.columns) >= _COLUMNS_TIMELINE

    def test_empty_providers_returns_empty_df(self, engine):
        from src.saas.presentation.streamlit.queries import _fetch_timeline_impl

        df = _fetch_timeline_impl(
            engine,
            acriss_code="IFAR",
            duration_days=_DURATION,
            providers=(),
            include_pending_review=True,
        )

        assert df.empty
        assert set(df.columns) >= _COLUMNS_TIMELINE

    def test_unknown_acriss_code_returns_empty_df(self, engine):
        from src.saas.presentation.streamlit.queries import _fetch_timeline_impl

        df = _fetch_timeline_impl(
            engine,
            acriss_code="ZZZZ",
            duration_days=_DURATION,
            providers=_PROVIDERS,
            include_pending_review=True,
        )

        assert df.empty


class TestFetchPvcDetails:
    def test_returns_dataframe_with_expected_columns(self, engine):
        from src.saas.presentation.streamlit.queries import _fetch_pvc_details_impl

        df = _fetch_pvc_details_impl(
            engine,
            acriss_code="IFAR",
            pickup_date=_PICKUP_DATE,
            duration_days=_DURATION,
            providers=_PROVIDERS,
            include_pending_review=True,
        )

        assert set(df.columns) >= _COLUMNS_DETAIL

    def test_empty_providers_returns_empty_df(self, engine):
        from src.saas.presentation.streamlit.queries import _fetch_pvc_details_impl

        df = _fetch_pvc_details_impl(
            engine,
            acriss_code="IFAR",
            pickup_date=_PICKUP_DATE,
            duration_days=_DURATION,
            providers=(),
            include_pending_review=True,
        )

        assert df.empty
        assert set(df.columns) >= _COLUMNS_DETAIL

    def test_unknown_acriss_code_returns_empty_df(self, engine):
        from src.saas.presentation.streamlit.queries import _fetch_pvc_details_impl

        df = _fetch_pvc_details_impl(
            engine,
            acriss_code="ZZZZ",
            pickup_date=_PICKUP_DATE,
            duration_days=_DURATION,
            providers=_PROVIDERS,
            include_pending_review=True,
        )

        assert df.empty
