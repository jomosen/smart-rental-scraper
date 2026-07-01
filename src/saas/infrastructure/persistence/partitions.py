"""Idempotent maintenance of monthly range partitions for time-partitioned tables.

`price_observations` is RANGE-partitioned by `observed_at`, one partition per
month. The create-tables migration only seeds the current + next month at
install time, with no ongoing automation — so once a run's `observed_at` crosses
into a month that has no partition, every INSERT fails with
`no partition of relation "price_observations" found for row`.

`ensure_price_observation_partitions()` creates the current and next month's
partitions when missing. Call it once at pipeline startup, before any write.
Idempotent: `CREATE TABLE IF NOT EXISTS` on deterministic per-month names, so
concurrent runs and repeated calls are safe.

Must run as the table OWNER (`smart_rental_admin`) — `CREATE ... PARTITION OF`
requires ownership — so pass `admin_engine()`, not the super/app engine. New
partitions inherit access through the partitioned parent, so no extra GRANTs to
the app role are needed (INSERTs are authorised on the parent table).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine


def _first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def _next_month(d: date) -> date:
    """First day of the month after *d*'s month (handles the Dec → Jan rollover)."""
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def ensure_price_observation_partitions(
    engine: Engine, today: date | None = None
) -> list[str]:
    """Create the current + next month partitions of `price_observations` if missing.

    Returns the partition table names that were ensured (created or already
    present). Runs as the connection's role — use `admin_engine()` (the owner).
    """
    month = _first_of_month(today or date.today())
    ensured: list[str] = []
    with engine.begin() as conn:
        for _ in range(2):  # current month + next month (boundary buffer)
            nxt = _next_month(month)
            name = f"price_observations_{month.year:04d}_{month.month:02d}"
            conn.execute(text(
                f"CREATE TABLE IF NOT EXISTS {name} "
                f"PARTITION OF price_observations "
                f"FOR VALUES FROM ('{month.isoformat()}') TO ('{nxt.isoformat()}')"
            ))
            ensured.append(name)
            month = nxt
    return ensured
