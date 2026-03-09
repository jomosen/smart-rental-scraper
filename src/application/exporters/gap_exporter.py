import csv
import json
from datetime import datetime
from typing import List, Tuple

from ...domain.models.result import BookingResult
from ...domain.models.search import BookingSearch

BookingPair = Tuple[BookingSearch, BookingResult]


class GapExporter:
    """Exports searches that ended with no results or with an error."""

    @staticmethod
    def _collect_gaps(pairs: List[BookingPair]) -> List[BookingPair]:
        return [
            (search, res) for search, res in pairs
            if res is None or res.errors or not res.cars
        ]

    @staticmethod
    def to_csv(pairs: List[BookingPair], path: str) -> int:
        """Writes the gap report to CSV. Returns the number of gaps."""
        gaps = GapExporter._collect_gaps(pairs)
        fieldnames = ["provider", "pickup_date", "dropoff_date", "duration_days", "error"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for search, res in gaps:
                error = "; ".join(res.errors) if (res and res.errors) else "no_results"
                writer.writerow({
                    "provider": search.provider.name,
                    "pickup_date": search.pickup_at.strftime("%Y-%m-%d"),
                    "dropoff_date": search.dropoff_at.strftime("%Y-%m-%d"),
                    "duration_days": search.rental_days,
                    "error": error,
                })
        return len(gaps)

    @staticmethod
    def to_json(pairs: List[BookingPair], path: str) -> int:
        """Writes the gap report to JSON. Returns the number of gaps."""
        gaps = GapExporter._collect_gaps(pairs)
        output = {
            "generated_at": datetime.now().isoformat(),
            "total_gaps": len(gaps),
            "gaps": [
                {
                    "provider": search.provider.name,
                    "pickup_date": search.pickup_at.isoformat(),
                    "dropoff_date": search.dropoff_at.isoformat(),
                    "duration_days": search.rental_days,
                    "error": "; ".join(res.errors) if (res and res.errors) else "no_results",
                }
                for search, res in gaps
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        return len(gaps)
