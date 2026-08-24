"""Re-classify active provider vehicle categories and diff the results against
what is persisted — WITHOUT writing anything.

Two modes:
  default   — the currently configured Gemini models (validate a model upgrade)
  --engine  — the deterministic ACRISS engine v2, no LLM at all (shadow run
              before flipping the classifier; see DATA_MODEL.md Decision 12)

Usage:
    python scripts/reclassify_compare.py [provider_code] [--engine]

Reads GEMINI_FLASH_MODEL / GEMINI_PRO_MODEL from the environment (.env).
Adoption path after a clean diff: the next pipeline run re-classifies changed
groups, and /api/v1/classify repopulates its cache under the new
classifier_version automatically.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from dotenv import load_dotenv
load_dotenv()

import psycopg

from src.saas.application.classification.acriss_loader import load_acriss_specs
from src.saas.application.classification.dtos import VehicleClassificationInput
from src.saas.infrastructure.classification.gemini_service import (
    GeminiClassificationService,
    flash_model,
    pro_model,
)

_YAML_PATH = Path(__file__).parents[1] / "acriss_codes.yaml"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    use_engine = "--engine" in sys.argv[1:]
    provider_filter = args[0] if args else None

    if use_engine:
        print("classifier: deterministic ACRISS engine v2 (no LLM)\n")
    else:
        print(f"models: flash={flash_model()!r}  pro={pro_model()!r}\n")

    conn = psycopg.connect(os.environ["SUPER_DATABASE_URL"].replace("+psycopg", ""))
    sql = """
        select p.code, pvc.external_code, pvc.external_name, pvc.example_models,
               pvc.seats, pvc.luggage, pvc.transmission,
               pvc.acriss_code, pvc.classification_confidence, pvc.pending_review
        from provider_vehicle_categories pvc
        join providers p on p.id = pvc.provider_id
        where pvc.active
    """
    params: tuple = ()
    if provider_filter:
        sql += " and p.code = %s"
        params = (provider_filter,)
    sql += " order by p.code, pvc.external_code"
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        sys.exit("no active provider_vehicle_categories found")

    if use_engine:
        from src.saas.infrastructure.classification.engine_service import (
            AcrissEngineClassificationService,
        )
        service = AcrissEngineClassificationService()  # no resolver, no queue
    else:
        service = GeminiClassificationService(acriss_types=load_acriss_specs(_YAML_PATH))

    by_provider: dict[str, list] = {}
    for r in rows:
        by_provider.setdefault(r[0], []).append(r)

    changed = same = 0
    for code, group_rows in by_provider.items():
        inputs = [
            VehicleClassificationInput(
                external_code=r[1],
                external_name=r[2] or r[1],
                example_models=r[3] or "",
                seats=r[4],
                luggage=r[5],
                transmission=r[6],
                fuel_type=None,
                representative_price_7d=None,
                representative_currency=None,
            )
            for r in group_rows
        ]
        results = service.classify_provider_batch(code, inputs)

        print(f"── {code} ({len(group_rows)} groups) " + "─" * 30)
        for r, new in zip(group_rows, results):
            old_code, old_conf, old_pending = r[7], r[8], r[9]
            parts = [new.acriss_category, new.acriss_body_type,
                     new.acriss_transmission, new.acriss_fuel]
            new_code = "".join(parts) if all(parts) else None
            delta = "=" if new_code == old_code else "≠"
            if new_code == old_code:
                same += 1
            else:
                changed += 1
            print(f"  {delta} {r[1]:8} old={old_code or '—':5} "
                  f"(c={float(old_conf or 0):.2f} pr={old_pending})  "
                  f"new={new_code or '—':5} "
                  f"(c={new.confidence:.2f} pr={new.pending_review})")
            if new_code != old_code:
                print(f"      reasoning: {(new.rationale or '')[:160]}")

    print(f"\nTOTAL: {same} unchanged, {changed} changed — nothing was written.")
    if changed:
        print("Review the changed rows before adopting the new models.")


if __name__ == "__main__":
    main()
