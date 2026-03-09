import csv
import json
from datetime import datetime
from typing import List, Tuple

from ...domain.models.result import BookingResult
from ...domain.models.search import BookingSearch

# Pure domain pair: (BookingSearch, BookingResult).
# ResultExporter does not depend on the SearchRequest application wrapper.
BookingPair = Tuple[BookingSearch, BookingResult]


class ResultExporter:

    @staticmethod
    def to_csv(pairs: List[BookingPair], path: str) -> None:
        """Exports denormalised results: one row per provider+duration+car+rate."""
        fieldnames = [
            "provider", "pickup_location", "dropoff_location",
            "pickup_date", "dropoff_date", "rental_days",
            "model", "group", "rate_name", "daily_price", "total", "currency",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for search, res in pairs:
                if not res or not res.cars:
                    continue
                for car in res.cars:
                    for rate in car.rates:
                        writer.writerow({
                            "provider": res.provider.name,
                            "pickup_location": search.pickup_location.display_name,
                            "dropoff_location": search.dropoff_location.display_name,
                            "pickup_date": search.pickup_at.strftime("%Y-%m-%d %H:%M"),
                            "dropoff_date": search.dropoff_at.strftime("%Y-%m-%d %H:%M"),
                            "rental_days": search.rental_days,
                            "model": car.model,
                            "group": car.group,
                            "rate_name": rate.name,
                            "daily_price": rate.daily_price,
                            "total": rate.total,
                            "currency": rate.currency,
                        })

    @staticmethod
    def to_json(pairs: List[BookingPair], path: str) -> None:
        """Exports results in a normalised hierarchical structure:
        generated_at → results[] → provider / dates / cars[] → rates[]
        """
        results = []
        for search, res in pairs:
            if not res or not res.cars:
                continue
            results.append({
                "provider": res.provider.name,
                "pickup_location": search.pickup_location.display_name,
                "dropoff_location": search.dropoff_location.display_name,
                "pickup_date": search.pickup_at.isoformat(),
                "dropoff_date": search.dropoff_at.isoformat(),
                "rental_days": search.rental_days,
                "cars": [
                    {
                        "model": car.model,
                        "group": car.group,
                        "rates": [
                            {
                                "name": rate.name,
                                "currency": rate.currency,
                                "daily_price": rate.daily_price,
                                "total": rate.total,
                            }
                            for rate in car.rates
                        ],
                    }
                    for car in res.cars
                ],
            })

        output = {
            "generated_at": datetime.now().isoformat(),
            "results": results,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
