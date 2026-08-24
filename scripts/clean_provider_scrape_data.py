"""Delete a provider's scraped data (observations, heartbeats, zones) from the
DB that the environment points at. Catalog rows survive: provider, locations,
rates, vehicle categories (classification included) and scrape_runs history.

Typical use: a provider's first runs captured the wrong tariff or bad prices
and the series must restart clean before re-scraping.

Usage:
    python scripts/clean_provider_scrape_data.py <provider_code>        # dry-run
    python scripts/clean_provider_scrape_data.py <provider_code> --yes  # delete

Points at whatever SUPER_DATABASE_URL says (.env = dev). To run against prod,
load .env.prod into the environment and open the SSH tunnel first — same
pattern as deploy/run_scraper_prod.ps1.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import psycopg
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("provider_code")
    ap.add_argument("--yes", action="store_true", help="actually delete (default: dry-run)")
    args = ap.parse_args()

    url = os.environ["SUPER_DATABASE_URL"].replace("+psycopg", "")
    conn = psycopg.connect(url)

    row = conn.execute(
        "select id from providers where code = %s", (args.provider_code,)
    ).fetchone()
    if not row:
        sys.exit(f"provider '{args.provider_code}' not found")
    pid = row[0]

    hb_cols = {r[0] for r in conn.execute(
        "select column_name from information_schema.columns "
        "where table_name='price_observation_heartbeats'"
    ).fetchall()}
    hb_where = (
        "provider_id = %s" if "provider_id" in hb_cols
        else "price_observation_id in (select id from price_observations where provider_id = %s)"
    )

    counts = {
        "price_observation_heartbeats": conn.execute(
            f"select count(*) from price_observation_heartbeats where {hb_where}", (pid,)
        ).fetchone()[0],
        "price_observations": conn.execute(
            "select count(*) from price_observations where provider_id = %s", (pid,)
        ).fetchone()[0],
        "homogeneous_zones": conn.execute(
            "select count(*) from homogeneous_zones where provider_vehicle_category_id in "
            "(select id from provider_vehicle_categories where provider_id = %s)", (pid,)
        ).fetchone()[0],
    }
    for table, n in counts.items():
        print(f"{table}: {n} row(s)")

    if not args.yes:
        print("\ndry-run — nothing deleted. Re-run with --yes to delete.")
        return

    conn.execute(f"delete from price_observation_heartbeats where {hb_where}", (pid,))
    conn.execute("delete from price_observations where provider_id = %s", (pid,))
    conn.execute(
        "delete from homogeneous_zones where provider_vehicle_category_id in "
        "(select id from provider_vehicle_categories where provider_id = %s)", (pid,)
    )
    conn.commit()
    print(f"\ndeleted — '{args.provider_code}' scrape data removed, catalog kept")


if __name__ == "__main__":
    main()
