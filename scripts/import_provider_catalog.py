"""Import a provider catalog exported by scripts/export_provider_catalog.py.

Idempotent upserts through natural keys — safe to re-run:
  - providers            by code
  - provider_locations   by (provider, location_code); canonical market location
                         resolved by locations.code (created if missing)
  - provider_rates       by (provider, rate_code)
  - provider_recipes     new version appended (active) only when recipe_jsonb
                         differs from the current active one
  - provider_vehicle_categories
                         by (location, rate, external_code | attributes_hash)

No price data is touched. Dry-run by default; --yes applies.

Usage:
    python scripts/import_provider_catalog.py provider_catalog_<code>.json [--yes]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.models import (
    Provider,
    ProviderLocation,
    ProviderRate,
    ProviderRecipe,
    ProviderVehicleCategory,
)
from src.saas.infrastructure.persistence.models.catalog import Location


def _pvc_key(location_code, rate_code, external_code, attributes_hash):
    return (location_code, rate_code, external_code or f"hash:{attributes_hash}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path")
    ap.add_argument("--yes", action="store_true", help="apply (default: dry-run)")
    args = ap.parse_args()

    doc = json.loads(Path(args.path).read_text(encoding="utf-8"))
    if doc.get("format") != "provider-catalog/1":
        print(f"ERROR: unrecognized format {doc.get('format')!r}")
        sys.exit(1)

    stats: list[str] = []
    factory = sessionmaker(bind=super_engine())
    with factory() as session:
        # ── Provider ─────────────────────────────────────────────────────────
        pdoc = doc["provider"]
        provider = session.execute(
            select(Provider).where(Provider.code == pdoc["code"])
        ).scalar_one_or_none()
        if provider is None:
            provider = Provider(
                code=pdoc["code"], display_name=pdoc["display_name"],
                scraper_key=pdoc["scraper_key"], default_currency=pdoc["default_currency"],
                status=pdoc["status"], base_url=pdoc["base_url"],
            )
            session.add(provider)
            session.flush()
            stats.append(f"provider: INSERT {pdoc['code']}")
        else:
            changed = []
            for field in ("display_name", "scraper_key", "default_currency", "status", "base_url"):
                if getattr(provider, field) != pdoc[field]:
                    setattr(provider, field, pdoc[field])
                    changed.append(field)
            stats.append(
                f"provider: {'UPDATE ' + ','.join(changed) if changed else 'unchanged'} ({pdoc['code']})"
            )

        # ── Locations (+ canonical market mapping) ───────────────────────────
        loc_by_code: dict[str, ProviderLocation] = {}
        n_new = n_upd = 0
        for ldoc in doc["locations"]:
            canonical_id = None
            if ldoc.get("canonical_location_code"):
                canonical = session.execute(
                    select(Location).where(Location.code == ldoc["canonical_location_code"])
                ).scalar_one_or_none()
                if canonical is None:
                    canonical = Location(
                        code=ldoc["canonical_location_code"],
                        name=ldoc.get("canonical_location_name") or ldoc["canonical_location_code"],
                    )
                    session.add(canonical)
                    session.flush()
                    stats.append(f"locations(canonical): INSERT {canonical.code}")
                canonical_id = canonical.id

            loc = session.execute(
                select(ProviderLocation).where(
                    ProviderLocation.provider_id == provider.id,
                    ProviderLocation.location_code == ldoc["location_code"],
                )
            ).scalar_one_or_none()
            if loc is None:
                loc = ProviderLocation(
                    provider_id=provider.id, location_code=ldoc["location_code"],
                    location_name=ldoc["location_name"], country=ldoc["country"],
                    city=ldoc["city"], active=ldoc["active"], location_id=canonical_id,
                )
                session.add(loc)
                session.flush()
                n_new += 1
            else:
                before = (loc.location_name, loc.country, loc.city, loc.active, loc.location_id)
                loc.location_name = ldoc["location_name"]
                loc.country = ldoc["country"]
                loc.city = ldoc["city"]
                loc.active = ldoc["active"]
                loc.location_id = canonical_id
                if before != (loc.location_name, loc.country, loc.city, loc.active, loc.location_id):
                    n_upd += 1
            loc_by_code[loc.location_code] = loc
        stats.append(f"provider_locations: {n_new} inserted, {n_upd} updated, "
                     f"{len(doc['locations']) - n_new - n_upd} unchanged")

        # ── Rates ────────────────────────────────────────────────────────────
        rate_by_code: dict[str, ProviderRate] = {}
        n_new = n_upd = 0
        for rdoc in doc["rates"]:
            rate = session.execute(
                select(ProviderRate).where(
                    ProviderRate.provider_id == provider.id,
                    ProviderRate.rate_code == rdoc["rate_code"],
                )
            ).scalar_one_or_none()
            if rate is None:
                rate = ProviderRate(
                    provider_id=provider.id, rate_code=rdoc["rate_code"],
                    rate_name=rdoc["rate_name"], description=rdoc["description"],
                    active=rdoc["active"],
                )
                session.add(rate)
                session.flush()
                n_new += 1
            else:
                before = (rate.rate_name, rate.description, rate.active)
                rate.rate_name = rdoc["rate_name"]
                rate.description = rdoc["description"]
                rate.active = rdoc["active"]
                if before != (rate.rate_name, rate.description, rate.active):
                    n_upd += 1
            rate_by_code[rate.rate_code] = rate
        stats.append(f"provider_rates: {n_new} inserted, {n_upd} updated, "
                     f"{len(doc['rates']) - n_new - n_upd} unchanged")

        # ── Recipe (append-only versioning, same contract as the builder) ────
        if doc.get("recipe"):
            current = session.execute(
                select(ProviderRecipe).where(
                    ProviderRecipe.provider_id == provider.id,
                    ProviderRecipe.active == True,  # noqa: E712
                )
            ).scalar_one_or_none()
            if current is not None and current.recipe_jsonb == doc["recipe"]["recipe_jsonb"]:
                stats.append(f"recipe: unchanged (active v{current.version})")
            else:
                max_version = session.execute(
                    select(ProviderRecipe.version)
                    .where(ProviderRecipe.provider_id == provider.id)
                    .order_by(ProviderRecipe.version.desc())
                ).scalars().first() or 0
                if current is not None:
                    current.active = False
                discovered = doc["recipe"]["discovered_at"]
                session.add(ProviderRecipe(
                    provider_id=provider.id,
                    version=max_version + 1,
                    recipe_jsonb=doc["recipe"]["recipe_jsonb"],
                    discovered_at=datetime.fromisoformat(discovered) if discovered else None,
                    active=True,
                ))
                stats.append(f"recipe: INSERT v{max_version + 1} (active)"
                             + (f", deactivated v{current.version}" if current else ""))
        else:
            stats.append("recipe: none in export")

        # ── Vehicle categories ───────────────────────────────────────────────
        existing = session.execute(
            select(ProviderVehicleCategory)
            .where(ProviderVehicleCategory.provider_id == provider.id)
        ).scalars().all()
        loc_code_by_id = {loc.id: code for code, loc in loc_by_code.items()}
        rate_code_by_id = {rate.id: code for code, rate in rate_by_code.items()}
        by_key = {
            _pvc_key(
                loc_code_by_id.get(p.provider_location_id),
                rate_code_by_id.get(p.provider_rate_id),
                p.external_code, p.attributes_hash,
            ): p
            for p in existing
        }
        fields = (
            "acriss_category", "acriss_body_type", "acriss_transmission", "acriss_fuel",
            "classification_confidence", "pending_review", "classification_detail",
            "example_models", "seats", "luggage", "transmission",
            "external_name", "active",
        )
        n_new = n_upd = 0
        for vdoc in doc["vehicle_categories"]:
            key = _pvc_key(
                vdoc["location_code"], vdoc["rate_code"],
                vdoc["external_code"], vdoc["attributes_hash"],
            )
            pvc = by_key.get(key)
            if pvc is None:
                pvc = ProviderVehicleCategory(
                    provider_id=provider.id,
                    provider_location_id=loc_by_code[vdoc["location_code"]].id,
                    provider_rate_id=rate_by_code[vdoc["rate_code"]].id,
                    external_code=vdoc["external_code"],
                    attributes_hash=vdoc["attributes_hash"],
                    **{f: vdoc[f] for f in fields},
                )
                session.add(pvc)
                n_new += 1
            else:
                before = tuple(getattr(pvc, f) for f in fields)
                for f in fields:
                    setattr(pvc, f, vdoc[f])
                if before != tuple(getattr(pvc, f) for f in fields):
                    n_upd += 1
        stats.append(
            f"provider_vehicle_categories: {n_new} inserted, {n_upd} updated, "
            f"{len(doc['vehicle_categories']) - n_new - n_upd} unchanged"
        )

        print(("APPLY" if args.yes else "DRY-RUN") + f" — provider '{pdoc['code']}':")
        for line in stats:
            print("  " + line)
        if args.yes:
            session.commit()
            print("Committed.")
        else:
            session.rollback()
            print("Rolled back (re-run with --yes to apply).")


if __name__ == "__main__":
    main()
