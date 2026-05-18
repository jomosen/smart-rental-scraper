"""Read-only SQL queries for the Streamlit dashboard.

Each public fetch_* function is decorated with @st.cache_data (60s TTL) for
use inside the Streamlit app. The underlying _*_impl functions accept an engine
and are called directly in tests without the Streamlit cache layer.

Engine: super_engine (BYPASSRLS) — the MVP dashboard shows all providers
without tenant scoping.
TODO: switch to app_engine + tenant_context when the dashboard gains multi-tenant support.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import Engine, text

# Ensure project root is on the path when the module is imported stand-alone.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.saas.infrastructure.persistence.engine import super_engine

# ---------------------------------------------------------------------------
# Engine singleton (lazy, not Streamlit-cached so it works in tests too)
# ---------------------------------------------------------------------------

_engine_cache: Engine | None = None


def _get_engine() -> Engine:
    global _engine_cache
    if _engine_cache is None:
        _engine_cache = super_engine()
    return _engine_cache


# ---------------------------------------------------------------------------
# Helper queries (no caching — used to populate sidebar widgets)
# ---------------------------------------------------------------------------

def get_pickup_date_range() -> tuple[date, date]:
    """Return (min_start, max_end) of active homogeneous zones, or a default range."""
    sql = text("""
        SELECT MIN(start_date)::date, MAX(end_date)::date
        FROM homogeneous_zones
        WHERE active = TRUE
    """)
    with _get_engine().connect() as conn:
        row = conn.execute(sql).fetchone()
    if row and row[0] is not None:
        return row[0], row[1]
    today = date.today()
    from datetime import timedelta
    return today, today + timedelta(days=90)


def get_active_provider_codes() -> list[str]:
    """Return codes of all active providers that have classified PVCs."""
    sql = text("""
        SELECT DISTINCT p.code
        FROM providers p
        JOIN provider_vehicle_categories pvc ON pvc.provider_id = p.id
        WHERE p.status = 'active'
          AND pvc.acriss_code IS NOT NULL
          AND pvc.active = TRUE
        ORDER BY p.code
    """)
    with _get_engine().connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [r[0] for r in rows]


def get_acriss_codes_with_data() -> list[str]:
    """Return ACRISS codes that have at least one classified, active PVC."""
    return [code for code, _ in get_acriss_codes_with_display_names()]


@st.cache_data(ttl=60)
def get_acriss_codes_with_display_names() -> list[tuple[str, str]]:
    """Return (code, display_name) pairs for active codes that have PVC data, ordered by code."""
    sql = text("""
        SELECT DISTINCT ac.code, ac.display_name
        FROM acriss_codes ac
        JOIN provider_vehicle_categories pvc ON pvc.acriss_code = ac.code
        WHERE ac.active = TRUE
          AND pvc.active = TRUE
        ORDER BY ac.code
    """)
    with _get_engine().connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [(r[0], r[1]) for r in rows]


# ---------------------------------------------------------------------------
# Market overview
# ---------------------------------------------------------------------------

_OVERVIEW_COLUMNS = [
    "acriss_code", "display_name", "provider_code", "provider_name",
    "min_price_per_day", "max_price_per_day", "avg_price_per_day",
    "pvc_count", "has_pending_review",
]


def _fetch_market_overview_impl(
    engine: Engine,
    pickup_date: date,
    duration_days: int,
    providers: tuple[str, ...],
    acriss_categories: tuple[str, ...] | None,
    include_pending_review: bool,
) -> pd.DataFrame:
    if not providers:
        return pd.DataFrame(columns=_OVERVIEW_COLUMNS)

    conditions = [
        "pvc.acriss_code IS NOT NULL",
        "pvc.active = TRUE",
        "p.code = ANY(:providers)",
    ]
    params: dict = {
        "pickup_date": pickup_date,
        "duration_days": duration_days,
        "providers": list(providers),
    }

    if acriss_categories:
        conditions.append("pvc.acriss_code = ANY(:acriss_categories)")
        params["acriss_categories"] = list(acriss_categories)

    if not include_pending_review:
        conditions.append("pvc.pending_review = FALSE")

    where = " AND ".join(conditions)

    sql = text(f"""
        WITH zones AS (
            SELECT hz.provider_vehicle_category_id,
                   hz.representative_date
            FROM   homogeneous_zones hz
            WHERE  hz.active = TRUE
              AND  hz.start_date <= :pickup_date
              AND  hz.end_date   >= :pickup_date
        ),
        latest_obs AS (
            SELECT DISTINCT ON (provider_vehicle_category_id, pickup_date, duration_days)
                   provider_vehicle_category_id,
                   pickup_date,
                   price_per_day
            FROM   price_observations
            WHERE  duration_days = :duration_days
            ORDER  BY provider_vehicle_category_id, pickup_date, duration_days, observed_at DESC
        )
        SELECT pvc.acriss_code,
               ac.display_name,
               p.code                                    AS provider_code,
               p.display_name                            AS provider_name,
               MIN(lo.price_per_day)                     AS min_price_per_day,
               MAX(lo.price_per_day)                     AS max_price_per_day,
               ROUND(AVG(lo.price_per_day)::numeric, 2)  AS avg_price_per_day,
               COUNT(DISTINCT pvc.id)                    AS pvc_count,
               BOOL_OR(pvc.pending_review)               AS has_pending_review
        FROM   provider_vehicle_categories pvc
        JOIN   providers   p  ON p.id   = pvc.provider_id
        JOIN   acriss_codes ac ON ac.code = pvc.acriss_code
        JOIN   zones        z  ON z.provider_vehicle_category_id = pvc.id
        JOIN   latest_obs  lo  ON lo.provider_vehicle_category_id = pvc.id
                               AND lo.pickup_date = z.representative_date
        WHERE  {where}
        GROUP  BY pvc.acriss_code, ac.display_name, p.code, p.display_name
        ORDER  BY ac.display_name, p.code
    """)

    with engine.connect() as conn:
        result = conn.execute(sql, params)
        rows = result.fetchall()
        cols = list(result.keys())

    if not rows:
        return pd.DataFrame(columns=_OVERVIEW_COLUMNS)
    return pd.DataFrame(rows, columns=cols)


@st.cache_data(ttl=60)
def fetch_market_overview(
    pickup_date: date,
    duration_days: int,
    providers: tuple[str, ...],
    acriss_categories: tuple[str, ...] | None,
    include_pending_review: bool,
) -> pd.DataFrame:
    return _fetch_market_overview_impl(
        _get_engine(), pickup_date, duration_days,
        providers, acriss_categories, include_pending_review,
    )


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

_TIMELINE_COLUMNS = ["pickup_date", "provider_code", "provider_name", "price_per_day"]


def _fetch_timeline_impl(
    engine: Engine,
    acriss_code: str,
    duration_days: int,
    providers: tuple[str, ...],
    include_pending_review: bool,
) -> pd.DataFrame:
    if not providers:
        return pd.DataFrame(columns=_TIMELINE_COLUMNS)

    pending_clause = "" if include_pending_review else "AND pvc.pending_review = FALSE"

    sql = text(f"""
        WITH zone_dates AS (
            SELECT generate_series(hz.start_date, hz.end_date, '1 day'::interval)::date
                       AS pickup_date,
                   hz.provider_vehicle_category_id,
                   hz.representative_date
            FROM   homogeneous_zones hz
            JOIN   provider_vehicle_categories pvc
                       ON pvc.id = hz.provider_vehicle_category_id
            WHERE  hz.active       = TRUE
              AND  pvc.acriss_code = :acriss_code
              AND  pvc.active      = TRUE
              {pending_clause}
        ),
        latest_obs AS (
            SELECT DISTINCT ON (provider_vehicle_category_id, pickup_date, duration_days)
                   provider_vehicle_category_id,
                   pickup_date,
                   price_per_day
            FROM   price_observations
            WHERE  duration_days = :duration_days
            ORDER  BY provider_vehicle_category_id, pickup_date, duration_days, observed_at DESC
        )
        SELECT zd.pickup_date,
               p.code         AS provider_code,
               p.display_name AS provider_name,
               MIN(lo.price_per_day) AS price_per_day
        FROM   zone_dates zd
        JOIN   provider_vehicle_categories pvc ON pvc.id = zd.provider_vehicle_category_id
        JOIN   providers p                     ON p.id   = pvc.provider_id
        JOIN   latest_obs lo
                   ON lo.provider_vehicle_category_id = zd.provider_vehicle_category_id
                  AND lo.pickup_date                  = zd.representative_date
        WHERE  p.code = ANY(:providers)
        GROUP  BY zd.pickup_date, p.code, p.display_name
        ORDER  BY zd.pickup_date, p.code
    """)

    params = {
        "acriss_code": acriss_code,
        "duration_days": duration_days,
        "providers": list(providers),
    }

    with engine.connect() as conn:
        result = conn.execute(sql, params)
        rows = result.fetchall()
        cols = list(result.keys())

    if not rows:
        return pd.DataFrame(columns=_TIMELINE_COLUMNS)
    return pd.DataFrame(rows, columns=cols)


@st.cache_data(ttl=60)
def fetch_timeline(
    acriss_code: str,
    duration_days: int,
    providers: tuple[str, ...],
    include_pending_review: bool,
) -> pd.DataFrame:
    return _fetch_timeline_impl(
        _get_engine(), acriss_code, duration_days, providers, include_pending_review,
    )


# ---------------------------------------------------------------------------
# PVC detail
# ---------------------------------------------------------------------------

_DETAIL_COLUMNS = [
    "provider_code", "external_code", "example_models", "seats",
    "current_price_per_day", "pending_review", "classification_confidence",
]


def _fetch_pvc_details_impl(
    engine: Engine,
    acriss_code: str,
    pickup_date: date,
    duration_days: int,
    providers: tuple[str, ...],
    include_pending_review: bool,
) -> pd.DataFrame:
    if not providers:
        return pd.DataFrame(columns=_DETAIL_COLUMNS)

    pending_clause = "" if include_pending_review else "AND pvc.pending_review = FALSE"

    sql = text(f"""
        WITH zones AS (
            SELECT hz.provider_vehicle_category_id,
                   hz.representative_date
            FROM   homogeneous_zones hz
            JOIN   provider_vehicle_categories pvc
                       ON pvc.id = hz.provider_vehicle_category_id
            WHERE  hz.active       = TRUE
              AND  hz.start_date  <= :pickup_date
              AND  hz.end_date    >= :pickup_date
              AND  pvc.acriss_code = :acriss_code
        ),
        latest_obs AS (
            SELECT DISTINCT ON (provider_vehicle_category_id, pickup_date, duration_days)
                   provider_vehicle_category_id,
                   pickup_date,
                   price_per_day
            FROM   price_observations
            WHERE  duration_days = :duration_days
            ORDER  BY provider_vehicle_category_id, pickup_date, duration_days, observed_at DESC
        )
        SELECT p.code         AS provider_code,
               pvc.external_code,
               pvc.example_models,
               pvc.seats,
               lo.price_per_day AS current_price_per_day,
               pvc.pending_review,
               pvc.classification_confidence
        FROM   provider_vehicle_categories pvc
        JOIN   providers p ON p.id = pvc.provider_id
        JOIN   zones     z ON z.provider_vehicle_category_id = pvc.id
        JOIN   latest_obs lo
                   ON lo.provider_vehicle_category_id = pvc.id
                  AND lo.pickup_date                  = z.representative_date
        WHERE  pvc.acriss_code = :acriss_code
          AND  pvc.active      = TRUE
          AND  p.code          = ANY(:providers)
          {pending_clause}
        ORDER  BY p.code, pvc.external_code
    """)

    params = {
        "acriss_code": acriss_code,
        "pickup_date": pickup_date,
        "duration_days": duration_days,
        "providers": list(providers),
    }

    with engine.connect() as conn:
        result = conn.execute(sql, params)
        rows = result.fetchall()
        cols = list(result.keys())

    if not rows:
        return pd.DataFrame(columns=_DETAIL_COLUMNS)
    return pd.DataFrame(rows, columns=cols)


@st.cache_data(ttl=60)
def fetch_pvc_details(
    acriss_code: str,
    pickup_date: date,
    duration_days: int,
    providers: tuple[str, ...],
    include_pending_review: bool,
) -> pd.DataFrame:
    return _fetch_pvc_details_impl(
        _get_engine(), acriss_code, pickup_date, duration_days,
        providers, include_pending_review,
    )
