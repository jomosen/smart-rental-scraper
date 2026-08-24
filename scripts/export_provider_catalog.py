"""Export one provider's catalog to a portable JSON file.

Covers: providers row, provider_locations (with the canonical market-location
code for remapping), provider_rates, the ACTIVE provider_recipes row, and
provider_vehicle_categories with their full classification. No price data —
zones/observations/heartbeats are produced by scraping on the target side.

IDs are never exported; the import script remaps everything through natural
keys (providers.code, location_code, rate_code, external_code/attributes_hash).

Usage:
    python scripts/export_provider_catalog.py <provider_code> [--out PATH]

Pair: scripts/import_provider_catalog.py
"""
from __future__ import annotations

import argparse
import json
import sys
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("provider_code")
    ap.add_argument("--out", default=None, help="output path (default provider_catalog_<code>.json)")
    args = ap.parse_args()

    out_path = Path(args.out or f"provider_catalog_{args.provider_code}.json")
    factory = sessionmaker(bind=super_engine())

    with factory() as session:
        provider = session.execute(
            select(Provider).where(Provider.code == args.provider_code)
        ).scalar_one_or_none()
        if provider is None:
            print(f"ERROR: provider '{args.provider_code}' not found")
            sys.exit(1)

        locations = session.execute(
            select(ProviderLocation).where(ProviderLocation.provider_id == provider.id)
        ).scalars().all()
        canonical_by_id = {
            loc.id: code
            for loc, code in session.execute(
                select(ProviderLocation, Location.code)
                .join(Location, ProviderLocation.location_id == Location.id)
                .where(ProviderLocation.provider_id == provider.id)
            )
        }
        canonical_names = {
            code: name for code, name in session.execute(select(Location.code, Location.name))
        }
        rates = session.execute(
            select(ProviderRate).where(ProviderRate.provider_id == provider.id)
        ).scalars().all()
        recipe = session.execute(
            select(ProviderRecipe)
            .where(ProviderRecipe.provider_id == provider.id, ProviderRecipe.active == True)  # noqa: E712
        ).scalar_one_or_none()
        pvcs = session.execute(
            select(ProviderVehicleCategory)
            .where(ProviderVehicleCategory.provider_id == provider.id)
        ).scalars().all()

        loc_code_by_id = {loc.id: loc.location_code for loc in locations}
        rate_code_by_id = {r.id: r.rate_code for r in rates}

        doc = {
            "format": "provider-catalog/1",
            "provider": {
                "code": provider.code,
                "display_name": provider.display_name,
                "scraper_key": provider.scraper_key,
                "default_currency": provider.default_currency,
                "status": provider.status,
                "base_url": provider.base_url,
            },
            "locations": [
                {
                    "location_code": loc.location_code,
                    "location_name": loc.location_name,
                    "country": loc.country,
                    "city": loc.city,
                    "active": loc.active,
                    "canonical_location_code": canonical_by_id.get(loc.id),
                    "canonical_location_name": canonical_names.get(canonical_by_id.get(loc.id)),
                }
                for loc in locations
            ],
            "rates": [
                {
                    "rate_code": r.rate_code,
                    "rate_name": r.rate_name,
                    "description": r.description,
                    "active": r.active,
                }
                for r in rates
            ],
            "recipe": (
                {
                    "recipe_jsonb": recipe.recipe_jsonb,
                    "discovered_at": recipe.discovered_at.isoformat() if recipe.discovered_at else None,
                    "source_version": recipe.version,
                }
                if recipe else None
            ),
            "vehicle_categories": [
                {
                    "location_code": loc_code_by_id[p.provider_location_id],
                    "rate_code": rate_code_by_id[p.provider_rate_id],
                    "external_code": p.external_code,
                    "external_name": p.external_name,
                    "attributes_hash": p.attributes_hash,
                    "acriss_category": p.acriss_category,
                    "acriss_body_type": p.acriss_body_type,
                    "acriss_transmission": p.acriss_transmission,
                    "acriss_fuel": p.acriss_fuel,
                    "classification_confidence": p.classification_confidence,
                    "pending_review": p.pending_review,
                    "classification_detail": p.classification_detail,
                    "example_models": p.example_models,
                    "seats": p.seats,
                    "luggage": p.luggage,
                    "transmission": p.transmission,
                    "active": p.active,
                }
                for p in pvcs
            ],
        }

    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Exported '{provider.code}' -> {out_path}\n"
        f"  locations={len(doc['locations'])} rates={len(doc['rates'])} "
        f"recipe={'v' + str(recipe.version) if recipe else 'NONE'} "
        f"vehicle_categories={len(doc['vehicle_categories'])}"
    )
    if recipe is None:
        print("WARNING: no active recipe — the target will not be scrapeable until one exists.")


if __name__ == "__main__":
    main()
