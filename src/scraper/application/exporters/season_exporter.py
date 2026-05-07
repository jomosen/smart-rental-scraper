import csv
import json
from collections import defaultdict
from datetime import datetime
from typing import List, Tuple

from ....shared.domain.models.season import HomogeneousZone

# Type: (provider_name, zones)
ProviderZones = Tuple[str, List[HomogeneousZone]]


class SeasonExporter:

    @staticmethod
    def to_csv(provider_zones: List[ProviderZones], path: str) -> None:
        """Exports season zones by group: one row per provider × group × zone."""
        fieldnames = [
            "provider", "car_group",
            "zone_start", "zone_end", "zone_days",
            "reference_price", "representative_date",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for provider_name, zones in provider_zones:
                for zone in zones:
                    writer.writerow({
                        "provider": provider_name,
                        "car_group": zone.car_group,
                        "zone_start": zone.start_date.strftime("%Y-%m-%d"),
                        "zone_end": zone.end_date.strftime("%Y-%m-%d"),
                        "zone_days": zone.days,
                        "reference_price": zone.reference_price,
                        "representative_date": zone.representative_date.strftime("%Y-%m-%d"),
                    })

    @staticmethod
    def to_csv_unified(provider_zones: List[ProviderZones], path: str) -> None:
        """Exports the actual extraction plan: unique representative dates across all
        groups, one row per provider × representative_date."""
        fieldnames = ["provider", "representative_date", "car_groups"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for provider_name, zones in provider_zones:
                by_date: dict = defaultdict(set)
                for zone in zones:
                    by_date[zone.representative_date].add(zone.car_group)
                for rep_date in sorted(by_date):
                    writer.writerow({
                        "provider": provider_name,
                        "representative_date": rep_date.strftime("%Y-%m-%d"),
                        "car_groups": ", ".join(sorted(by_date[rep_date])),
                    })

    @staticmethod
    def to_json(provider_zones: List[ProviderZones], path: str) -> None:
        """Exports zones by group in a hierarchical structure per provider."""
        output = {
            "generated_at": datetime.now().isoformat(),
            "providers": [
                {
                    "provider": provider_name,
                    "zones": [
                        {
                            "car_group": zone.car_group,
                            "zone_start": zone.start_date.isoformat(),
                            "zone_end": zone.end_date.isoformat(),
                            "zone_days": zone.days,
                            "reference_price": float(zone.reference_price),
                            "representative_date": zone.representative_date.isoformat(),
                        }
                        for zone in zones
                    ],
                }
                for provider_name, zones in provider_zones
                if zones
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

    @staticmethod
    def to_json_unified(provider_zones: List[ProviderZones], path: str) -> None:
        """Exports the actual extraction plan in a hierarchical structure per provider."""
        output = {
            "generated_at": datetime.now().isoformat(),
            "providers": [
                {
                    "provider": provider_name,
                    "extraction_dates": [
                        {
                            "representative_date": rep_date.isoformat(),
                            "car_groups": sorted(groups),
                        }
                        for rep_date, groups in sorted(
                            {
                                z.representative_date: {
                                    z2.car_group
                                    for z2 in zones
                                    if z2.representative_date == z.representative_date
                                }
                                for z in zones
                            }.items()
                        )
                    ],
                }
                for provider_name, zones in provider_zones
                if zones
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
