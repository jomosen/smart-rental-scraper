from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from typing import List

from ...domain.interfaces.smart_scraping import ISeasonBoundaryRepository
from ...domain.models.season_internals import SeasonBoundary
from ....shared.domain.models.season import HomogeneousZone

logger = logging.getLogger(__name__)

_DATE_FMT = "%Y-%m-%d"
_TS_FMT = "%Y-%m-%dT%H:%M:%S"


class JsonSeasonBoundaryRepository(ISeasonBoundaryRepository):
    """
    Persists season boundaries in a JSON file per provider.

    File structure (seasons/{provider_slug}.json):
    {
      "entries": [
        {
          "provider": "...",
          "car_group": "Grupo B",
          "period_start": "2026-04-01",
          "period_end": "2026-04-30",
          "detected_at": "2026-03-09T10:00:00",
          "boundaries": [...],
          "zones": [...]
        }
      ]
    }

    The repository stores multiple entries per provider (different
    periods and groups). load() returns the entry whose period matches
    exactly the one requested.
    """

    def __init__(self, storage_dir: str = "seasons") -> None:
        self._storage_dir = storage_dir
        self._lock = asyncio.Lock()
        os.makedirs(storage_dir, exist_ok=True)

    async def save(
        self,
        provider: str,
        car_group: str,
        boundaries: List[SeasonBoundary],
        zones: List[HomogeneousZone],
        period_start: date,
        period_end: date,
    ) -> None:
        async with self._lock:
            path = self._path(provider)
            data = self._read_file(path)

            entry = {
                "provider": provider,
                "car_group": car_group,
                "period_start": period_start.strftime(_DATE_FMT),
                "period_end": period_end.strftime(_DATE_FMT),
                "detected_at": datetime.now().strftime(_TS_FMT),
                "boundaries": [self._serialize_boundary(b) for b in boundaries],
                "zones": [self._serialize_zone(z) for z in zones],
            }

            entries: List[dict] = data.get("entries", [])
            entries = [
                e for e in entries
                if not (
                    e["car_group"] == car_group
                    and e["period_start"] == entry["period_start"]
                    and e["period_end"] == entry["period_end"]
                )
            ]
            entries.append(entry)
            data["entries"] = entries

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(
            "[%s] Seasons saved: %d zones for %s (%s → %s)",
            provider, len(zones), car_group,
            period_start.strftime(_DATE_FMT), period_end.strftime(_DATE_FMT),
        )

    def _path(self, provider: str) -> str:
        slug = provider.lower().replace(" ", "_")
        return os.path.join(self._storage_dir, f"{slug}.json")

    def _read_file(self, path: str) -> dict:
        if not os.path.exists(path):
            return {"entries": []}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read %s: %s", path, exc)
            return {"entries": []}

    @staticmethod
    def _serialize_boundary(b: SeasonBoundary) -> dict:
        return {
            "left_date": b.left_date.strftime(_DATE_FMT),
            "right_date": b.right_date.strftime(_DATE_FMT),
            "left_price": b.left_price,
            "right_price": b.right_price,
        }

    @staticmethod
    def _serialize_zone(z: HomogeneousZone) -> dict:
        return {
            "start_date": z.start_date.strftime(_DATE_FMT),
            "end_date": z.end_date.strftime(_DATE_FMT),
            "reference_price": float(z.reference_price),
            "car_group": z.car_group,
            "representative_date": z.representative_date.strftime(_DATE_FMT),
        }

    @staticmethod
    def _deserialize_zone(d: dict) -> HomogeneousZone:
        return HomogeneousZone(
            start_date=datetime.strptime(d["start_date"], _DATE_FMT).date(),
            end_date=datetime.strptime(d["end_date"], _DATE_FMT).date(),
            reference_price=Decimal(str(d["reference_price"])),
            car_group=d["car_group"],
            representative_date=datetime.strptime(d["representative_date"], _DATE_FMT).date(),
        )
