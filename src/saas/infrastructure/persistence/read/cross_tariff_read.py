"""Read-only queries backing the cross-tariff view, presentation-agnostic.

These functions accept any object with an `.execute()` method that returns a
SQLAlchemy Result — a Connection or a Session both work. This lets the same SQL
serve:
  - the client API (app_engine session inside tenant_context, RLS-enforced), and
  - admin/back-office scripts (super_engine connection, BYPASSRLS).

All tables read here (providers, provider_vehicle_categories, homogeneous_zones,
price_observations, acriss_codes) are global catalog tables — not tenant-scoped,
so RLS does not filter them. Tenant isolation in the API affects only the
tenant-scoped tables read elsewhere (tenants, pricing_rules).
"""
from __future__ import annotations

import datetime
from typing import Protocol

import pandas as pd
from sqlalchemy import text

# Duration brackets shown across the grid. Single source of truth for the API
# and the CSV/PDF exports.
DURATIONS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 14, 21, 28)

CROSS_TARIFF_COLUMNS = [
    "acriss_code", "acriss_display_name",
    "provider_code", "external_code", "attributes_hash", "example_models",
    "transmission", "acriss_transmission", "pending_review",
    "start_date", "end_date", "representative_date",
    "duration_days", "price_per_day", "total_price",
]


class _Executor(Protocol):
    def execute(self, *args, **kwargs): ...


def fetch_cross_tariff_dataframe(
    conn: _Executor,
    provider_codes: tuple[str, ...],
    durations: tuple[int, ...] = DURATIONS,
    include_pending_review: bool = True,
    location_id: int | None = None,
) -> pd.DataFrame:
    """One row per (provider, acriss_code, zone, duration) latest observation.

    Zone alignment to a master provider happens in the assembler, not here.
    When `location_id` (a canonical market) is given, restrict to the provider
    offices mapped to it; unmapped offices are excluded. None = all offices.
    """
    if not provider_codes:
        return pd.DataFrame(columns=CROSS_TARIFF_COLUMNS)

    acriss_clause = ""
    pending_clause = "" if include_pending_review else "AND pvc.pending_review = FALSE"

    # Canonical-market filter: resolve to the provider_locations mapped to it.
    obs_loc_clause = ""
    zone_loc_clause = ""
    params: dict = {
        "provider_codes": list(provider_codes),
        "durations": list(durations),
    }
    if location_id is not None:
        params["location_id"] = location_id
        obs_loc_clause = (
            "AND po.provider_location_id IN "
            "(SELECT id FROM provider_locations WHERE location_id = :location_id)"
        )
        zone_loc_clause = (
            "AND hz.provider_location_id IN "
            "(SELECT id FROM provider_locations WHERE location_id = :location_id)"
        )

    sql = text(f"""
        WITH latest_obs AS (
            SELECT DISTINCT ON (po.provider_vehicle_category_id, po.pickup_date, po.duration_days)
                   po.provider_vehicle_category_id,
                   po.pickup_date,
                   po.duration_days,
                   po.price_per_day,
                   po.total_price
            FROM   price_observations po
            JOIN   providers p ON p.id = po.provider_id
            WHERE  p.code = ANY(:provider_codes)
              AND  po.duration_days = ANY(:durations)
              {obs_loc_clause}
            ORDER  BY po.provider_vehicle_category_id, po.pickup_date,
                      po.duration_days, po.observed_at DESC
        ),
        zones AS (
            SELECT pvc.acriss_code,
                   ac.display_name  AS acriss_display_name,
                   pvc.external_code,
                   pvc.attributes_hash,
                   pvc.example_models,
                   pvc.transmission,
                   pvc.acriss_transmission,
                   pvc.pending_review,
                   hz.start_date,
                   hz.end_date,
                   hz.representative_date,
                   hz.id            AS zone_id,
                   hz.provider_vehicle_category_id,
                   p.code           AS provider_code
            FROM   homogeneous_zones hz
            JOIN   provider_vehicle_categories pvc ON pvc.id = hz.provider_vehicle_category_id
            JOIN   providers    p   ON p.id    = pvc.provider_id
            JOIN   acriss_codes ac  ON ac.code = pvc.acriss_code
            WHERE  hz.active       = TRUE
              AND  pvc.active      = TRUE
              AND  pvc.acriss_code IS NOT NULL
              AND  p.code          = ANY(:provider_codes)
              {acriss_clause}
              {pending_clause}
              {zone_loc_clause}
        ),
        obs_in_zone AS (
            -- Back each zone with the observation at its representative_date, or
            -- failing that the CLOSEST observation WITHIN [start_date, end_date].
            -- A homogeneous zone is ~flat in price by design, so any in-range
            -- observation is representative. Deliberately never falls back to an
            -- observation OUTSIDE the zone (that would be another season).
            -- See docs/DATA_MODEL.md (homogeneous_zones — representative/derivation).
            SELECT DISTINCT ON (z.zone_id, lo.duration_days)
                   z.zone_id,
                   lo.duration_days,
                   lo.price_per_day,
                   lo.total_price
            FROM   zones z
            JOIN   latest_obs lo
                       ON lo.provider_vehicle_category_id = z.provider_vehicle_category_id
                      AND lo.pickup_date BETWEEN z.start_date AND z.end_date
            ORDER  BY z.zone_id, lo.duration_days,
                      abs(lo.pickup_date - z.representative_date), lo.pickup_date
        )
        SELECT z.acriss_code,
               z.acriss_display_name,
               z.provider_code,
               z.external_code,
               z.attributes_hash,
               z.example_models,
               z.transmission,
               z.acriss_transmission,
               z.pending_review,
               z.start_date,
               z.end_date,
               z.representative_date,
               o.duration_days,
               o.price_per_day,
               o.total_price
        FROM   zones z
        JOIN   obs_in_zone o ON o.zone_id = z.zone_id
        ORDER  BY z.acriss_code, z.provider_code, z.external_code,
                  z.start_date, o.duration_days
    """)

    result = conn.execute(sql, params)
    rows = result.fetchall()
    cols = list(result.keys())
    if not rows:
        return pd.DataFrame(columns=CROSS_TARIFF_COLUMNS)
    return pd.DataFrame(rows, columns=cols)


def fetch_active_providers(conn: _Executor) -> list[tuple[str, str]]:
    """Return (code, display_name) for active providers that have classified PVCs."""
    sql = text("""
        SELECT DISTINCT p.code, p.display_name
        FROM   providers p
        JOIN   provider_vehicle_categories pvc ON pvc.provider_id = p.id
        WHERE  p.status = 'active'
          AND  pvc.acriss_code IS NOT NULL
          AND  pvc.active = TRUE
        ORDER  BY p.code
    """)
    rows = conn.execute(sql).fetchall()
    return [(r[0], r[1]) for r in rows]


def fetch_provider_groups(
    conn: _Executor,
    provider_codes: tuple[str, ...] | None = None,
    location_id: int | None = None,
) -> list[dict]:
    """Every active vehicle group of the active providers — the matching catalog.

    One entry per *logical* group, not per `provider_vehicle_categories` row: a
    PVC is identified by (provider, location, rate, external_code), so the same
    group appears once per office/rate.  Grouping on the identity key collapses
    those into a single selectable entry and reports the canonical markets it was
    seen in via `location_ids`.

    Unlike every other read here, groups with `acriss_code IS NULL` are NOT
    filtered out: group-to-group matching does not go through ACRISS, so an
    unclassified group is still a valid target.  Callers that need the ACRISS
    layer can filter on the nullable `acriss_code` / `pending_review` fields.
    """
    params: dict = {}
    provider_clause = ""
    if provider_codes is not None:
        if not provider_codes:
            return []
        provider_clause = "AND p.code = ANY(:provider_codes)"
        params["provider_codes"] = list(provider_codes)

    location_clause = ""
    if location_id is not None:
        location_clause = "AND pl.location_id = :location_id"
        params["location_id"] = location_id

    sql = text(f"""
        SELECT p.code                                              AS provider_code,
               p.display_name                                      AS provider_name,
               COALESCE(pvc.external_code, pvc.attributes_hash)     AS group_key,
               MAX(pvc.external_code)                              AS external_code,
               MAX(pvc.attributes_hash)                            AS attributes_hash,
               MAX(pvc.external_name)                              AS external_name,
               MAX(pvc.example_models)                             AS example_models,
               MAX(pvc.transmission)                               AS transmission,
               MAX(pvc.acriss_code)                                AS acriss_code,
               BOOL_OR(pvc.pending_review)                         AS pending_review,
               ARRAY_REMOVE(ARRAY_AGG(DISTINCT pl.location_id), NULL) AS location_ids
        FROM   provider_vehicle_categories pvc
        JOIN   providers p ON p.id = pvc.provider_id
        JOIN   provider_locations pl ON pl.id = pvc.provider_location_id
        WHERE  pvc.active = TRUE
          AND  p.status = 'active'
          {provider_clause}
          {location_clause}
        GROUP  BY p.code, p.display_name,
                  COALESCE(pvc.external_code, pvc.attributes_hash)
        ORDER  BY p.code, group_key
    """)
    return [
        {
            "provider_code": r.provider_code,
            "provider_name": r.provider_name,
            # Stable identity of the group within its provider: the provider's own
            # code when it exposes one, else the attributes hash.  This is what a
            # tenant→group mapping should persist.
            "group_key": r.group_key,
            "external_code": r.external_code,
            "attributes_hash": r.attributes_hash,
            "external_name": r.external_name,
            "models": _split_models(r.example_models),
            "transmission": r.transmission,
            "acriss_code": r.acriss_code,
            "pending_review": r.pending_review,
            "location_ids": list(r.location_ids or []),
        }
        for r in conn.execute(sql, params).fetchall()
    ]


def _split_models(example_models: str | None) -> list[str]:
    """Split the free-text `example_models` into individual model names.

    Providers list several models per group comma-separated ("FIAT PANDA, KIA
    PICANTO").  The column is free text, so this is a presentation convenience,
    not a parse with guarantees.
    """
    return [m.strip() for m in (example_models or "").split(",") if m.strip()]


def fetch_catalog_examples(conn: _Executor) -> dict[str, list[str]]:
    """Return {acriss_code: [example models]} from the catalog (acriss_codes.examples).

    This is the curated catalog list — NOT the example_models of individual PVCs.
    """
    sql = text("SELECT code, examples FROM acriss_codes WHERE active = TRUE")
    out: dict[str, list[str]] = {}
    for code, examples in conn.execute(sql).fetchall():
        out[code] = list(examples or [])
    return out


def fetch_data_updated_at(
    conn: _Executor, provider_codes: tuple[str, ...]
) -> datetime.datetime | None:
    """Return the most recent observed_at across the given providers, or None."""
    if not provider_codes:
        return None
    sql = text("""
        SELECT MAX(po.observed_at)
        FROM   price_observations po
        JOIN   providers p ON p.id = po.provider_id
        WHERE  p.code = ANY(:codes)
    """)
    return conn.execute(sql, {"codes": list(provider_codes)}).scalar()


def fetch_canonical_locations(conn: _Executor) -> list[dict]:
    """Canonical markets that have at least one mapped office with classified data.

    These are the options shown in the location selector. Unmapped provider
    offices (location_id IS NULL) never surface here.
    """
    sql = text("""
        SELECT DISTINCT l.id, l.code, l.name
        FROM   locations l
        JOIN   provider_locations pvl ON pvl.location_id = l.id
        JOIN   provider_vehicle_categories pvc ON pvc.provider_location_id = pvl.id
        WHERE  pvc.active = TRUE
          AND  pvc.acriss_code IS NOT NULL
        ORDER  BY l.name
    """)
    return [{"id": r.id, "code": r.code, "name": r.name} for r in conn.execute(sql).fetchall()]


def fetch_master_zone_covering(
    conn: _Executor, master_code: str, location_id: int | None, today
) -> dict | None:
    """Return {date_from, date_to} of the master's active zone covering `today`, or None.

    Used to label the data-gap notice: the genuinely-current season even when no
    master observation backs it (so it was skipped from the rendered grid).
    """
    loc = ""
    params = {"code": master_code, "today": today}
    if location_id is not None:
        loc = "AND pvc.provider_location_id IN (SELECT id FROM provider_locations WHERE location_id = :loc)"
        params["loc"] = location_id
    row = conn.execute(text(f"""
        SELECT hz.start_date, hz.end_date
        FROM   homogeneous_zones hz
        JOIN   provider_vehicle_categories pvc ON pvc.id = hz.provider_vehicle_category_id
        JOIN   providers p ON p.id = pvc.provider_id
        WHERE  hz.active = TRUE AND pvc.active = TRUE
          AND  p.code = :code
          AND  hz.start_date <= :today AND hz.end_date >= :today
          {loc}
        ORDER  BY hz.start_date
        LIMIT  1
    """), params).fetchone()
    return {"date_from": row.start_date.isoformat(), "date_to": row.end_date.isoformat()} if row else None


def fetch_location(conn: _Executor, location_id: int) -> dict | None:
    """Return a single canonical location {id, code, name}, or None if it doesn't exist."""
    row = conn.execute(
        text("SELECT id, code, name FROM locations WHERE id = :id"),
        {"id": location_id},
    ).fetchone()
    return {"id": row.id, "code": row.code, "name": row.name} if row else None
