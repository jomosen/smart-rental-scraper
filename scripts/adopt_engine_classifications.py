"""Force-refresh every active provider_vehicle_category through engine v2.

The orchestrator reuses cached classifications while a group's attribute hash
is unchanged, so flipping the classifier does NOT rewrite existing rows. This
script re-classifies all active groups with the engine and applies the result
using the SAME persistence semantics as production
(ProviderVehicleCategoryRepository._apply_classification): a pending result
never wipes an existing code — it keeps it and flags pending_review.

Usage:
    python scripts/adopt_engine_classifications.py            # dry-run
    python scripts/adopt_engine_classifications.py --yes      # apply
    python scripts/adopt_engine_classifications.py recordgo --yes

Unknown models hit the Gemini semantic resolver (cents) and are queued in
acriss_review_queue.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from src.saas.application.classification.dtos import VehicleClassificationInput
from src.saas.infrastructure.classification.engine_service import (
    AcrissEngineClassificationService,
)
from src.saas.infrastructure.classification.semantic_resolver import (
    SemanticModelResolver,
)
from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.models.catalog import (
    Provider,
    ProviderVehicleCategory,
)
from src.saas.infrastructure.persistence.repositories import (
    ProviderVehicleCategoryRepository,
)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--yes" in sys.argv[1:]
    provider_filter = args[0] if args else None

    factory = sessionmaker(bind=super_engine(), autocommit=False, autoflush=False)
    service = AcrissEngineClassificationService(
        resolver=SemanticModelResolver(),
        session_factory=factory,
    )

    with factory() as session:
        q = (
            select(Provider.code, ProviderVehicleCategory)
            .join(Provider, Provider.id == ProviderVehicleCategory.provider_id)
            .where(ProviderVehicleCategory.active.is_(True))
            .order_by(Provider.code, ProviderVehicleCategory.external_code)
        )
        if provider_filter:
            q = q.where(Provider.code == provider_filter)
        rows = session.execute(q).all()
        if not rows:
            sys.exit("no active provider_vehicle_categories found")

        by_provider: dict[str, list[ProviderVehicleCategory]] = {}
        for code, pvc in rows:
            by_provider.setdefault(code, []).append(pvc)

        repo = ProviderVehicleCategoryRepository(session)
        changed = kept = 0
        for code, pvcs in by_provider.items():
            inputs = [
                VehicleClassificationInput(
                    external_code=p.external_code,
                    external_name=p.external_name or p.external_code,
                    example_models=p.example_models or "",
                    seats=p.seats,
                    luggage=p.luggage,
                    transmission=p.transmission,
                    fuel_type=None,
                    representative_price_7d=None,
                    representative_currency=None,
                )
                for p in pvcs
            ]
            results = service.classify_provider_batch(code, inputs)
            print(f"── {code} ({len(pvcs)} groups)")
            for pvc, result in zip(pvcs, results):
                old = pvc.acriss_code
                new = (
                    (result.acriss_category or "")
                    + (result.acriss_body_type or "")
                    + (result.acriss_transmission or "")
                    + (result.acriss_fuel or "")
                ) or None
                delta = "=" if new == old else "≠"
                pend = " pr" if result.pending_review else ""
                print(f"  {delta} {pvc.external_code or pvc.attributes_hash:16} "
                      f"{old or '—':5} -> {new or '—':5}{pend}")
                if new == old:
                    kept += 1
                else:
                    changed += 1
                if apply:
                    # Same semantics as production writes: pending results
                    # keep an existing code and only raise the flag.
                    repo._apply_classification(pvc, result, is_new=False)

        if apply:
            session.commit()
            print(f"\nAPPLIED — {changed} changed, {kept} unchanged (production "
                  "semantics: pending results kept their cached code).")
        else:
            print(f"\nDRY-RUN — would change {changed}, keep {kept}. "
                  "Re-run with --yes to apply.")


if __name__ == "__main__":
    main()
