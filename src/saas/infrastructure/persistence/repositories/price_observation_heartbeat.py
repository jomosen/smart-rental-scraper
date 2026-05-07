from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models.catalog import PriceObservationHeartbeat


class PriceObservationHeartbeatRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert(
        self,
        provider_id: int,
        provider_location_id: int,
        provider_rate_id: int,
        provider_vehicle_group_id: int,
        pickup_date: datetime.date,
        duration_days: int,
        price_per_day: Decimal,
        checked_at: datetime.datetime,
    ) -> None:
        """Insert or update the heartbeat row using PostgreSQL ON CONFLICT DO UPDATE."""
        self._s.execute(
            text("""
                INSERT INTO price_observation_heartbeats
                    (provider_id, provider_location_id, provider_rate_id,
                     provider_vehicle_group_id, pickup_date, duration_days,
                     last_checked_at, last_price_per_day)
                VALUES
                    (:provider_id, :provider_location_id, :provider_rate_id,
                     :provider_vehicle_group_id, :pickup_date, :duration_days,
                     :last_checked_at, :last_price_per_day)
                ON CONFLICT (provider_id, provider_location_id, provider_rate_id,
                             provider_vehicle_group_id, pickup_date, duration_days)
                DO UPDATE SET
                    last_checked_at   = EXCLUDED.last_checked_at,
                    last_price_per_day = EXCLUDED.last_price_per_day
            """),
            {
                "provider_id": provider_id,
                "provider_location_id": provider_location_id,
                "provider_rate_id": provider_rate_id,
                "provider_vehicle_group_id": provider_vehicle_group_id,
                "pickup_date": pickup_date,
                "duration_days": duration_days,
                "last_checked_at": checked_at,
                "last_price_per_day": price_per_day,
            },
        )
