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
_PROVIDERS = ("provider_a", "victoria", "solcar")


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


# ---------------------------------------------------------------------------
# Tariff table
# ---------------------------------------------------------------------------

_COLUMNS_TARIFF = {
    "acriss_code", "acriss_display_name",
    "external_code", "example_models", "pending_review",
    "start_date", "end_date", "representative_date",
    "duration_days", "price_per_day", "total_price",
}

_TARIFF_PROVIDER = "tariff_test_sc"


@pytest.fixture(scope="class")
def tariff_fixtures(engine):
    """Insert test catalog + observations for tariff query tests, then clean up."""
    from sqlalchemy import text

    with engine.begin() as conn:
        prov_id = conn.execute(text("""
            INSERT INTO providers (code, display_name, scraper_key, default_currency, status, base_url)
            VALUES (:code, 'Tariff Test SC', 'recipe', 'EUR', 'active', '')
            RETURNING id
        """), {"code": _TARIFF_PROVIDER}).scalar()

        loc_id = conn.execute(text("""
            INSERT INTO provider_locations (provider_id, location_code, location_name)
            VALUES (:pid, 'TF', 'Test Location')
            RETURNING id
        """), {"pid": prov_id}).scalar()

        rate_id = conn.execute(text("""
            INSERT INTO provider_rates (provider_id, rate_code, rate_name)
            VALUES (:pid, 'default', 'Default')
            RETURNING id
        """), {"pid": prov_id}).scalar()

        # ECO (EDMR, pending=False) — lower price, used to verify MIN
        pvc_eco_id = conn.execute(text("""
            INSERT INTO provider_vehicle_categories
                (provider_id, provider_location_id, provider_rate_id,
                 acriss_category, acriss_body_type, acriss_transmission, acriss_fuel,
                 external_code, example_models, pending_review)
            VALUES (:pid, :lid, :rid, 'E', 'D', 'M', 'R', 'ECO', 'Seat Ibiza', FALSE)
            RETURNING id
        """), {"pid": prov_id, "lid": loc_id, "rid": rate_id}).scalar()

        # ECO2 (EDMR, pending=False) — higher price, MIN should prefer ECO
        pvc_eco2_id = conn.execute(text("""
            INSERT INTO provider_vehicle_categories
                (provider_id, provider_location_id, provider_rate_id,
                 acriss_category, acriss_body_type, acriss_transmission, acriss_fuel,
                 external_code, example_models, pending_review)
            VALUES (:pid, :lid, :rid, 'E', 'D', 'M', 'R', 'ECO2', 'Hyundai i20', FALSE)
            RETURNING id
        """), {"pid": prov_id, "lid": loc_id, "rid": rate_id}).scalar()

        # CMP (CDMR, pending=True) — used for pending_review filter test
        pvc_cmp_id = conn.execute(text("""
            INSERT INTO provider_vehicle_categories
                (provider_id, provider_location_id, provider_rate_id,
                 acriss_category, acriss_body_type, acriss_transmission, acriss_fuel,
                 external_code, example_models, pending_review)
            VALUES (:pid, :lid, :rid, 'C', 'D', 'M', 'R', 'CMP', 'Ford Focus', TRUE)
            RETURNING id
        """), {"pid": prov_id, "lid": loc_id, "rid": rate_id}).scalar()

        run_id = conn.execute(text("""
            INSERT INTO scrape_runs
                (provider_id, provider_location_id, provider_rate_id, status, started_at, finished_at)
            VALUES (:pid, :lid, :rid, 'success', NOW(), NOW())
            RETURNING id
        """), {"pid": prov_id, "lid": loc_id, "rid": rate_id}).scalar()

        for pvc_id in (pvc_eco_id, pvc_eco2_id, pvc_cmp_id):
            conn.execute(text("""
                INSERT INTO homogeneous_zones
                    (provider_id, provider_location_id, provider_rate_id,
                     provider_vehicle_category_id,
                     start_date, end_date, representative_date, active)
                VALUES (:pid, :lid, :rid, :pvc_id,
                        '2026-06-01', '2026-06-30', '2026-06-15', TRUE)
            """), {"pid": prov_id, "lid": loc_id, "rid": rate_id, "pvc_id": pvc_id})

        # ECO: cheaper ppd (20) but higher total (200)
        # ECO2: pricier ppd (30) but lower total (150)
        # This intentional mismatch lets the coherence test verify that
        # total_price comes from the same PVC as price_per_day (ECO → 200),
        # not from a separate MIN(total) across both PVCs (which would give 150).
        # EDMR 1d: ECO2 wins (ppd=25 < 35), paired total=25.
        obs_rows = [
            (pvc_eco_id,  7, "20.00", "200.00"),
            (pvc_eco_id,  1, "35.00",  "35.00"),
            (pvc_eco2_id, 7, "30.00", "150.00"),
            (pvc_eco2_id, 1, "25.00",  "25.00"),
            (pvc_cmp_id,  7, "50.00", "350.00"),
            (pvc_cmp_id,  1, "55.00",  "55.00"),
        ]
        for pvc_id, dur, ppd, total in obs_rows:
            conn.execute(text("""
                INSERT INTO price_observations
                    (provider_id, provider_location_id, provider_rate_id,
                     provider_vehicle_category_id, scrape_run_id,
                     pickup_date, duration_days, price_per_day, total_price, currency, observed_at)
                VALUES (:pid, :lid, :rid, :pvc_id, :run_id,
                        '2026-06-15', :dur, :ppd, :total, 'EUR', NOW())
            """), {
                "pid": prov_id, "lid": loc_id, "rid": rate_id,
                "pvc_id": pvc_id, "run_id": run_id,
                "dur": dur, "ppd": ppd, "total": total,
            })

    yield {"prov_id": prov_id}

    # Cleanup in FK-safe order
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM price_observations WHERE provider_id = :pid"), {"pid": prov_id})
        conn.execute(text("DELETE FROM homogeneous_zones WHERE provider_id = :pid"), {"pid": prov_id})
        conn.execute(text("DELETE FROM scrape_runs WHERE provider_id = :pid"), {"pid": prov_id})
        conn.execute(text("DELETE FROM provider_vehicle_categories WHERE provider_id = :pid"), {"pid": prov_id})
        conn.execute(text("DELETE FROM provider_rates WHERE provider_id = :pid"), {"pid": prov_id})
        conn.execute(text("DELETE FROM provider_locations WHERE provider_id = :pid"), {"pid": prov_id})
        conn.execute(text("DELETE FROM providers WHERE id = :pid"), {"pid": prov_id})


class TestFetchTariffTable:
    def test_returns_dataframe_with_expected_columns(self, engine, tariff_fixtures):
        from src.saas.presentation.streamlit.queries import _fetch_tariff_table_impl

        df = _fetch_tariff_table_impl(engine, _TARIFF_PROVIDER)

        assert set(df.columns) >= _COLUMNS_TARIFF

    def test_unknown_provider_returns_empty_df(self, engine):
        from src.saas.presentation.streamlit.queries import _fetch_tariff_table_impl

        df = _fetch_tariff_table_impl(engine, "no_such_provider_xyz")

        assert df.empty
        assert set(df.columns) >= _COLUMNS_TARIFF

    def test_two_pvcs_same_acriss_both_rows_returned(self, engine, tariff_fixtures):
        """Two PVCs share EDMR; both must appear as separate rows — no aggregation collapse."""
        from src.saas.presentation.streamlit.queries import _fetch_tariff_table_impl

        df = _fetch_tariff_table_impl(engine, _TARIFF_PROVIDER, durations=(7,))
        edmr = df[df["acriss_code"] == "EDMR"]

        assert len(edmr) == 2
        assert set(edmr["external_code"].unique()) == {"ECO", "ECO2"}
        ppd = dict(zip(edmr["external_code"], edmr["price_per_day"].astype(float)))
        assert ppd["ECO"] == pytest.approx(20.0)
        assert ppd["ECO2"] == pytest.approx(30.0)

    def test_pending_review_per_row_not_aggregated(self, engine, tariff_fixtures):
        """pending_review is per-row (not BOOL_OR): ECO is False, CMP is True."""
        from src.saas.presentation.streamlit.queries import _fetch_tariff_table_impl

        df = _fetch_tariff_table_impl(engine, _TARIFF_PROVIDER, durations=(7,))
        eco_row = df[df["external_code"] == "ECO"].iloc[0]
        cmp_row = df[df["external_code"] == "CMP"].iloc[0]

        assert eco_row["pending_review"] == False
        assert cmp_row["pending_review"] == True

    def test_exclude_pending_review_hides_pending_category(self, engine, tariff_fixtures):
        """CDMR is pending=True; excluding pending must drop it from the result."""
        from src.saas.presentation.streamlit.queries import _fetch_tariff_table_impl

        df_with = _fetch_tariff_table_impl(engine, _TARIFF_PROVIDER, include_pending_review=True)
        df_without = _fetch_tariff_table_impl(engine, _TARIFF_PROVIDER, include_pending_review=False)

        assert "CDMR" in df_with["acriss_code"].values
        assert "CDMR" not in df_without["acriss_code"].values

    def test_duration_filter_limits_returned_durations(self, engine, tariff_fixtures):
        """Passing durations=(7,) must produce only rows with duration_days==7."""
        from src.saas.presentation.streamlit.queries import _fetch_tariff_table_impl

        df = _fetch_tariff_table_impl(engine, _TARIFF_PROVIDER, durations=(7,))

        assert not df.empty
        assert set(df["duration_days"].unique()) == {7}
