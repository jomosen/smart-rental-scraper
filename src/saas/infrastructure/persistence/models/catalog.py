"""ORM models for the global (non-tenant) catalog tables.

These tables hold provider data shared across all tenants and are therefore
not subject to Row-Level Security. All columns mirror the Alembic migrations
exactly — types, nullability, and server_defaults are for documentation only;
the DB schema is authoritative.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class AcrissCode(Base):
    """Materialized subset of the ACRISS standard used by this platform.

    Only codes observed in the market are persisted.  Source of truth: acriss_codes.yaml.
    """
    __tablename__ = "acriss_codes"

    code: Mapped[str] = mapped_column(String(4), primary_key=True)
    acriss_category: Mapped[str] = mapped_column(CHAR(1), nullable=False)
    acriss_body_type: Mapped[str] = mapped_column(CHAR(1), nullable=False)
    acriss_transmission: Mapped[str] = mapped_column(CHAR(1), nullable=False)
    acriss_fuel: Mapped[str] = mapped_column(CHAR(1), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    criteria: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="[]")
    examples: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="[]")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )
    last_updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )


class Provider(Base):
    __tablename__ = "providers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'beta', 'deprecated', 'broken')",
            name="ck_providers_status",
        ),
        UniqueConstraint("code", name="uq_providers_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    scraper_key: Mapped[str] = mapped_column(String(64), nullable=False)
    default_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    base_url: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )

    locations: Mapped[list[ProviderLocation]] = relationship(back_populates="provider")
    rates: Mapped[list[ProviderRate]] = relationship(back_populates="provider")
    vehicle_categories: Mapped[list[ProviderVehicleCategory]] = relationship(
        back_populates="provider"
    )


class Location(Base):
    """Canonical market location (catalog, no tenant_id, no RLS).

    The market where provider offices compete (e.g. "alc-airport"). Distinct from
    provider_locations, which keep each provider's own naming. See
    docs/DATA_MODEL.md §"Canonical market locations (catalog)".
    """
    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("code", name="uq_locations_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )


class ProviderLocation(Base):
    __tablename__ = "provider_locations"
    __table_args__ = (
        UniqueConstraint(
            "provider_id", "location_code",
            name="uq_provider_locations_provider_location",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("providers.id", name="fk_provider_locations_provider", ondelete="RESTRICT"), nullable=False
    )
    location_code: Mapped[str] = mapped_column(String(16), nullable=False)
    location_name: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # n:1 mapping to the canonical market; NULL = unmapped (excluded from filtered views).
    location_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("locations.id", name="fk_provider_locations_location", ondelete="SET NULL"),
        nullable=True,
    )

    provider: Mapped[Provider] = relationship(back_populates="locations")


class ProviderRate(Base):
    __tablename__ = "provider_rates"
    __table_args__ = (
        UniqueConstraint(
            "provider_id", "rate_code",
            name="uq_provider_rates_provider_rate",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("providers.id", name="fk_provider_rates_provider", ondelete="RESTRICT"), nullable=False
    )
    rate_code: Mapped[str] = mapped_column(String(32), nullable=False)
    rate_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    provider: Mapped[Provider] = relationship(back_populates="rates")


class ProviderVehicleCategory(Base):
    """One row per (provider, location, rate, external_code/attributes_hash) tuple.

    external_code / external_name are kept as documentary metadata when the
    provider exposes internal identifiers.  Classification into an ACRISS code
    is performed by ClassificationService.
    """
    __tablename__ = "provider_vehicle_categories"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("providers.id", name="fk_provider_vehicle_groups_provider", ondelete="RESTRICT"), nullable=False
    )
    provider_location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_locations.id", name="fk_provider_vehicle_groups_location", ondelete="RESTRICT"), nullable=False
    )
    provider_rate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_rates.id", name="fk_provider_vehicle_groups_rate", ondelete="RESTRICT"), nullable=False
    )
    # ACRISS classification (4 orthogonal attributes + generated code)
    acriss_category: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    acriss_body_type: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    acriss_transmission: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    acriss_fuel: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    acriss_code: Mapped[Optional[str]] = mapped_column(
        String(4),
        Computed(
            "acriss_category || acriss_body_type || acriss_transmission || acriss_fuel",
            persisted=True,
        ),
        ForeignKey("acriss_codes.code", name="fk_pvc_acriss_code"),
        nullable=True,
    )
    classification_confidence: Mapped[Optional[float]] = mapped_column(
        Float(precision=53), nullable=True
    )
    pending_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # Observed display attributes (last seen)
    example_models: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    seats: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    luggage: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    transmission: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # Documentary metadata (nullable — not all providers expose codes)
    external_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    external_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Identity fallback when external_code is NULL (sha256[:16] over attributes)
    attributes_hash: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # Lifecycle
    first_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    provider: Mapped[Provider] = relationship(back_populates="vehicle_categories")


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'failed')",
            name="ck_scrape_runs_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("providers.id", name="fk_scrape_runs_provider", ondelete="RESTRICT"), nullable=False
    )
    provider_location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_locations.id", name="fk_scrape_runs_location", ondelete="RESTRICT"), nullable=False
    )
    provider_rate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_rates.id", name="fk_scrape_runs_rate", ondelete="RESTRICT"), nullable=False
    )
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    stats_jsonb: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class HomogeneousZone(Base):
    __tablename__ = "homogeneous_zones"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_homogeneous_zones_date_order"),
        CheckConstraint(
            "representative_date BETWEEN start_date AND end_date",
            name="ck_homogeneous_zones_representative_in_zone",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("providers.id", name="fk_homogeneous_zones_provider", ondelete="RESTRICT"), nullable=False
    )
    provider_location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_locations.id", name="fk_homogeneous_zones_location", ondelete="RESTRICT"), nullable=False
    )
    provider_rate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_rates.id", name="fk_homogeneous_zones_rate", ondelete="RESTRICT"), nullable=False
    )
    provider_vehicle_category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_vehicle_categories.id", name="fk_homogeneous_zones_vehicle_group", ondelete="RESTRICT"), nullable=False
    )
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    end_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    representative_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    # Guide-group price/day that defined the zone. Used to carry forward a
    # season's start_date across scrapes when the leading zone is price-continuous.
    reference_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    detected_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class PriceObservation(Base):
    """Maps to the partitioned price_observations table.

    The composite primary key (id, observed_at) is required by Postgres for
    partitioned tables — the partition key must be part of the PK.
    SQLAlchemy treats this as a regular composite PK for ORM purposes.
    """
    __tablename__ = "price_observations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("providers.id", name="fk_price_observations_provider", ondelete="RESTRICT"), nullable=False
    )
    provider_location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_locations.id", name="fk_price_observations_location", ondelete="RESTRICT"), nullable=False
    )
    provider_rate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_rates.id", name="fk_price_observations_rate", ondelete="RESTRICT"), nullable=False
    )
    provider_vehicle_category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_vehicle_categories.id", name="fk_price_observations_vehicle_group", ondelete="RESTRICT"), nullable=False
    )
    scrape_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("scrape_runs.id", name="fk_price_observations_scrape_run", ondelete="RESTRICT"), nullable=False
    )
    pickup_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    duration_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    price_per_day: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    total_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    observed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, primary_key=True
    )


class PriceObservationHeartbeat(Base):
    """One row per (provider, location, rate, vehicle_category, pickup_date, duration).

    Updated in place on every scrape to avoid redundant price_observations
    inserts when the price has not changed.
    """
    __tablename__ = "price_observation_heartbeats"

    provider_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("providers.id", name="fk_heartbeats_provider", ondelete="RESTRICT"), nullable=False, primary_key=True
    )
    provider_location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_locations.id", name="fk_heartbeats_location", ondelete="RESTRICT"), nullable=False, primary_key=True
    )
    provider_rate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_rates.id", name="fk_heartbeats_rate", ondelete="RESTRICT"), nullable=False, primary_key=True
    )
    provider_vehicle_category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_vehicle_categories.id", name="fk_heartbeats_vehicle_group", ondelete="RESTRICT"), nullable=False, primary_key=True
    )
    pickup_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, primary_key=True)
    duration_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, primary_key=True)
    last_checked_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_price_per_day: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)


class ProviderRecipe(Base):
    """Versioned scraping recipe for one provider.

    Append-only versioning: re-running the builder inserts a new row with
    version = max(version) + 1 and active = true; the previous active row is
    flipped to active = false in the same transaction.  A partial unique index
    (provider_id WHERE active = true) ensures at most one active recipe per
    provider at all times.

    recipe_jsonb stores the complete Recipe domain object serialised as JSONB
    by ProviderRecipeRepository.  No tenant_id; no RLS — catalog scope.
    """
    __tablename__ = "provider_recipes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("providers.id", name="fk_provider_recipes_provider", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    recipe_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False)
    discovered_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )
