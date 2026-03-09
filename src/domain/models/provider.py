from dataclasses import dataclass


@dataclass(frozen=True)
class BookingProvider:
    name: str
    base_url: str
