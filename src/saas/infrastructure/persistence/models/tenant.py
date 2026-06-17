"""ORM models for multi-tenant tables.

All tables here are protected by Row-Level Security. The app user
(smart_rental_app) must have app.tenant_id set via set_config() before
any DML — see session.tenant_context().

Column types, nullability, and constraints mirror the Alembic migration
exactly. The DB schema is always authoritative.
"""
from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        # No UniqueConstraint needed: PK on id is sufficient.
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default="gen_random_uuid()")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, server_default="mvp")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )

    users: Mapped[list[User]] = relationship(back_populates="tenant")
    subscriptions: Mapped[list[TenantSubscription]] = relationship(back_populates="tenant")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('owner')", name="ck_users_role"),
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default="gen_random_uuid()")
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_users_tenant", ondelete="RESTRICT"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    external_auth_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, server_default="owner")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class TenantVehicleGroup(Base):
    """Optional per-tenant vehicle taxonomy layer.

    Tenants that use the canonical taxonomy directly do not need to create
    any rows here.  Only tenants with their own internal naming populate
    this table and link via TenantVehicleGroupMapping.
    """
    __tablename__ = "tenant_vehicle_groups"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "code", name="uq_client_vehicle_groups_tenant_code"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default="gen_random_uuid()")
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_client_vehicle_groups_tenant", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )


class TenantVehicleGroupMapping(Base):
    """Maps a tenant's vehicle group label onto one or more ACRISS codes.

    N:M: a tenant group may cover multiple ACRISS codes, and the same
    ACRISS code may appear in multiple tenant groups (rare but valid).
    """
    __tablename__ = "tenant_vehicle_group_mappings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "tenant_vehicle_group_id", "acriss_code",
            name="uq_tenant_vehicle_group_mappings_tuple",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default="gen_random_uuid()")
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_vehicle_group_mappings_tenant", ondelete="RESTRICT"), nullable=False
    )
    tenant_vehicle_group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant_vehicle_groups.id", name="fk_vehicle_group_mappings_client_group", ondelete="RESTRICT"), nullable=False
    )
    acriss_code: Mapped[str] = mapped_column(
        String(4),
        ForeignKey("acriss_codes.code", name="fk_tvgm_acriss_code", ondelete="RESTRICT"),
        nullable=False,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", name="fk_vehicle_group_mappings_created_by", ondelete="RESTRICT"), nullable=True
    )


class TenantSubscription(Base):
    __tablename__ = "tenant_subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_discovery', 'pending_mapping', 'active',"
            " 'paused', 'cancelled', 'broken')",
            name="ck_tenant_subscriptions_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default="gen_random_uuid()")
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_tenant_subscriptions_tenant", ondelete="RESTRICT"), nullable=False
    )
    provider_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("providers.id", name="fk_tenant_subscriptions_provider", ondelete="RESTRICT"),
        nullable=False,
    )
    provider_location_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("provider_locations.id", name="fk_tenant_subscriptions_location", ondelete="RESTRICT"),
        nullable=False,
    )
    provider_rate_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("provider_rates.id", name="fk_tenant_subscriptions_rate", ondelete="RESTRICT"),
        nullable=False,
    )
    period_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="90")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending_discovery")
    subscribed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )
    cancelled_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="subscriptions")


class PricingRule(Base):
    """Versioned cross-provider pricing configuration for one tenant.

    acriss_code is NULL for a "global config" rule (one rule covers all
    categories, with per-category overrides stored inside formula_jsonb).
    See MILESTONES.md D5: versioning is of the complete configuration, not
    per category — per-category versioning would require a different model.
    """
    __tablename__ = "pricing_rules"
    __table_args__ = (
        CheckConstraint(
            "floor IS NULL OR ceiling IS NULL OR floor <= ceiling",
            name="ck_pricing_rules_floor_ceiling",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default="gen_random_uuid()")
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_pricing_rules_tenant", ondelete="RESTRICT"), nullable=False
    )
    acriss_code: Mapped[Optional[str]] = mapped_column(
        String(4),
        ForeignKey("acriss_codes.code", name="fk_pricing_rules_acriss_code", ondelete="RESTRICT"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    condition_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    formula_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False)
    floor: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    ceiling: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )
    superseded_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("pricing_rules.id", name="fk_pricing_rules_superseded_by", ondelete="RESTRICT"), nullable=True
    )


class PricingOutput(Base):
    __tablename__ = "pricing_outputs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default="gen_random_uuid()")
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_pricing_outputs_tenant", ondelete="RESTRICT"), nullable=False
    )
    acriss_code: Mapped[Optional[str]] = mapped_column(
        String(4),
        ForeignKey("acriss_codes.code", name="fk_pricing_outputs_acriss_code", ondelete="RESTRICT"),
        nullable=True,
    )
    pickup_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    rule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pricing_rules.id", name="fk_pricing_outputs_rule", ondelete="RESTRICT"), nullable=False
    )
    inputs_snapshot_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )
