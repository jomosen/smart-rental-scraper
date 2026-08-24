"""Read-only queries backing the provider-groups catalog API.

`/api/v1/prices` answers "what do these groups cost"; this module answers "what
groups exist and how far ahead do they have prices". It feeds the matching
selector of an external system: the full catalog, including groups with no
price in any current season and groups classification left unclassified.

Same executor convention as cross_tariff_read: any object with `.execute()`
works (Session or Connection).

Group identity: one entry per *logical* group, not per
`provider_vehicle_categories` row. A PVC is one (provider, location, rate,
external_code) tuple, so the same commercial group repeats per office/rate;
grouping on COALESCE(external_code, attributes_hash) collapses those into one
selectable entry, reporting the canonical markets it was seen in.

Coverage semantics (all computed against `today`, so never cache the result):
  - ranges: the group's active seasons ending today or later, clipped to start
    no earlier than today, each flagged `priced` = has at least one observation
    of the reference duration inside the season's full bounds (same in-zone
    fallback semantics the price reads use).
  - covered_days: days inside priced ranges (overlaps merged — future
    multi-office groups must not double-count).
  - through / horizon_days: last day of the latest priced range, and the
    inclusive day count from today to it. Day counts are inclusive on both
    ends so a gap-free group has covered_days == horizon_days.
  - by_duration: covered_days recomputed per canonical duration bracket —
    coverage differs per duration, a single number would lie.
"""
from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import text

from .cross_tariff_read import DURATIONS, _Executor


def split_models(example_models: str | None) -> list[str]:
    """Split the free-text `example_models` into individual model names.

    Providers list several models per group comma-separated ("FIAT PANDA, KIA
    PICANTO"). The column is free text, so this is a presentation convenience,
    not a parse with guarantees.
    """
    return [m.strip() for m in (example_models or "").split(",") if m.strip()]


# ACRISS mainstream/elite scale (docs/acriss_reference.md): each Elite sits
# immediately above its mainstream counterpart. Codes sort by this scale so a
# rendered catalog reads smallest-to-largest; letters outside the scale (new
# categories not yet ranked) sink to the end rather than erroring.
_CATEGORY_SCALE = "MNEHCDIJSRFGPULWX"


def _catalog_sort_key(code: str) -> tuple:
    cat = code[0]
    pos = _CATEGORY_SCALE.index(cat) if cat in _CATEGORY_SCALE else len(_CATEGORY_SCALE)
    return (pos, code[1:])


def fetch_acriss_catalog(conn: _Executor) -> list[dict]:
    """The active materialized ACRISS catalog, for external selectors.

    One entry per active `acriss_codes` row: the four letters, curated display
    name / description / criteria / examples (source of truth:
    acriss_codes.yaml), plus `group_count` — how many active logical provider
    groups are currently classified into the code (same logical-group identity
    as fetch_provider_groups). `group_count = 0` marks a code with no market
    presence today; consumers can grey it out but should keep it selectable.

    Ordered by the ACRISS mainstream/elite scale, then code — stable for
    direct rendering.
    """
    sql = text("""
        SELECT ac.code,
               ac.acriss_category,
               ac.acriss_body_type,
               ac.acriss_transmission,
               ac.acriss_fuel,
               ac.display_name,
               ac.description,
               ac.criteria,
               ac.examples,
               COALESCE(gc.group_count, 0) AS group_count
        FROM   acriss_codes ac
        LEFT   JOIN (
            SELECT pvc.acriss_code,
                   COUNT(DISTINCT (p.code, COALESCE(pvc.external_code, pvc.attributes_hash)))
                       AS group_count
            FROM   provider_vehicle_categories pvc
            JOIN   providers p ON p.id = pvc.provider_id
            WHERE  pvc.active = TRUE
              AND  p.status = 'active'
              AND  pvc.acriss_code IS NOT NULL
            GROUP  BY pvc.acriss_code
        ) gc ON gc.acriss_code = ac.code
        WHERE  ac.active = TRUE
    """)
    rows = conn.execute(sql).fetchall()
    entries = [
        {
            "code": r.code,
            "category": r.acriss_category,
            "body_type": r.acriss_body_type,
            "transmission": r.acriss_transmission,
            "fuel": r.acriss_fuel,
            "display_name": r.display_name,
            "description": (r.description or "").strip(),
            "criteria": list(r.criteria or []),
            "examples": list(r.examples or []),
            "group_count": r.group_count,
        }
        for r in rows
    ]
    return sorted(entries, key=lambda e: _catalog_sort_key(e["code"]))


def fetch_provider_groups(
    conn: _Executor,
    provider_codes: tuple[str, ...] | None = None,
    location_id: int | None = None,
) -> list[dict]:
    """Every active vehicle group of the active providers — the matching catalog.

    Unlike the price reads, groups with `acriss_code IS NULL` are included:
    group-to-group matching does not go through ACRISS, so an unclassified
    group is still a valid target.
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
        SELECT p.code                                               AS provider_code,
               p.display_name                                       AS provider_name,
               COALESCE(pvc.external_code, pvc.attributes_hash)     AS group_key,
               MAX(pvc.external_code)                               AS external_code,
               MAX(pvc.attributes_hash)                             AS attributes_hash,
               MAX(pvc.external_name)                               AS external_name,
               MAX(pvc.example_models)                              AS example_models,
               MAX(pvc.transmission)                                AS transmission,
               MAX(pvc.acriss_code)                                 AS acriss_code,
               BOOL_OR(pvc.pending_review)                          AS pending_review,
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
            # Stable identity of the group within its provider — what a
            # client-side mapping should persist.
            "group_key": r.group_key,
            "external_code": r.external_code,
            "attributes_hash": r.attributes_hash,
            "external_name": r.external_name,
            "models": split_models(r.example_models),
            "transmission": r.transmission,
            "acriss_code": r.acriss_code,
            "pending_review": r.pending_review,
            "location_ids": list(r.location_ids or []),
        }
        for r in conn.execute(sql, params).fetchall()
    ]


def fetch_group_coverage(
    conn: _Executor,
    reference_duration: int,
    today: datetime.date | None = None,
    provider_codes: tuple[str, ...] | None = None,
    location_id: int | None = None,
    durations: tuple[int, ...] = DURATIONS,
) -> dict[tuple[str, str], dict]:
    """Coverage per logical group, keyed by (provider_code, group_key).

    Groups with no active season ending today or later are simply absent —
    callers render the empty coverage (see `empty_coverage`).
    """
    today = today or datetime.date.today()
    params: dict = {"today": today, "durations": list(durations)}

    provider_clause = ""
    if provider_codes is not None:
        if not provider_codes:
            return {}
        provider_clause = "AND p.code = ANY(:provider_codes)"
        params["provider_codes"] = list(provider_codes)

    location_clause = ""
    if location_id is not None:
        location_clause = (
            "AND hz.provider_location_id IN "
            "(SELECT id FROM provider_locations WHERE location_id = :location_id)"
        )
        params["location_id"] = location_id

    # One row per current-or-future season, with the set of duration brackets
    # that have at least one observation inside the season's full bounds.
    zones_sql = text(f"""
        SELECT p.code                                           AS provider_code,
               COALESCE(pvc.external_code, pvc.attributes_hash) AS group_key,
               hz.start_date,
               hz.end_date,
               ARRAY(
                   SELECT DISTINCT po.duration_days
                   FROM   price_observations po
                   WHERE  po.provider_vehicle_category_id = hz.provider_vehicle_category_id
                     AND  po.pickup_date BETWEEN hz.start_date AND hz.end_date
                     AND  po.duration_days = ANY(:durations)
               ) AS backed_durations
        FROM   homogeneous_zones hz
        JOIN   provider_vehicle_categories pvc ON pvc.id = hz.provider_vehicle_category_id
        JOIN   providers p ON p.id = pvc.provider_id
        WHERE  hz.active = TRUE
          AND  pvc.active = TRUE
          AND  p.status = 'active'
          AND  hz.end_date >= :today
          {provider_clause}
          {location_clause}
        ORDER  BY p.code, group_key, hz.start_date
    """)

    observed_sql = text(f"""
        SELECT p.code                                           AS provider_code,
               COALESCE(pvc.external_code, pvc.attributes_hash) AS group_key,
               MAX(po.observed_at)                              AS last_observed_at
        FROM   price_observations po
        JOIN   provider_vehicle_categories pvc ON pvc.id = po.provider_vehicle_category_id
        JOIN   providers p ON p.id = pvc.provider_id
        WHERE  pvc.active = TRUE
          AND  p.status = 'active'
          {provider_clause}
        GROUP  BY p.code, COALESCE(pvc.external_code, pvc.attributes_hash)
    """)

    zones_by_group: dict[tuple[str, str], list] = {}
    for r in conn.execute(zones_sql, params).fetchall():
        zones_by_group.setdefault((r.provider_code, r.group_key), []).append(r)

    observed_params = {k: v for k, v in params.items() if k != "location_id"}
    last_observed = {
        (r.provider_code, r.group_key): r.last_observed_at
        for r in conn.execute(observed_sql, observed_params).fetchall()
    }

    out: dict[tuple[str, str], dict] = {}
    for key, zones in zones_by_group.items():
        ranges = []
        priced_intervals: dict[int, list[tuple[datetime.date, datetime.date]]] = {
            d: [] for d in durations
        }
        for z in zones:
            clipped_start = max(z.start_date, today)
            backed = set(z.backed_durations or [])
            ranges.append({
                "from": clipped_start.isoformat(),
                "through": z.end_date.isoformat(),
                "priced": reference_duration in backed,
            })
            for d in backed:
                priced_intervals[d].append((clipped_start, z.end_date))

        by_duration = {
            str(d): _merged_days(priced_intervals[d]) for d in durations
        }
        ref_intervals = priced_intervals[reference_duration]
        through = max((end for _, end in ref_intervals), default=None)
        out[key] = {
            "from": today.isoformat(),
            "through": through.isoformat() if through else None,
            "covered_days": by_duration[str(reference_duration)],
            "horizon_days": (through - today).days + 1 if through else 0,
            "by_duration": by_duration,
            "ranges": ranges,
            "last_observed_at": (
                last_observed[key].isoformat() if last_observed.get(key) else None
            ),
        }
    return out


def empty_coverage(today: datetime.date, durations: tuple[int, ...] = DURATIONS) -> dict:
    """Coverage shape for a group with no current or future season."""
    return {
        "from": today.isoformat(),
        "through": None,
        "covered_days": 0,
        "horizon_days": 0,
        "by_duration": {str(d): 0 for d in durations},
        "ranges": [],
        "last_observed_at": None,
    }


def _merged_days(intervals: list[tuple[datetime.date, datetime.date]]) -> int:
    """Total days covered by the intervals, inclusive, overlaps counted once.

    Seasons of one office never overlap, but a logical group spanning several
    offices yields one interval list per office — summing without merging would
    double-count the shared days.
    """
    if not intervals:
        return 0
    total = 0
    current_start: Optional[datetime.date] = None
    current_end: Optional[datetime.date] = None
    for start, end in sorted(intervals):
        if end < start:
            continue
        if current_end is None:
            current_start, current_end = start, end
        elif start <= current_end + datetime.timedelta(days=1):
            current_end = max(current_end, end)
        else:
            total += (current_end - current_start).days + 1
            current_start, current_end = start, end
    if current_end is not None:
        total += (current_end - current_start).days + 1
    return total
