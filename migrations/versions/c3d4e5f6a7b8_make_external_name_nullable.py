"""make external_name nullable in provider_vehicle_categories

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-05-16

Aligns the DB column with the SQLAlchemy model, which declares
external_name as Optional[str] (nullable=True). The column was
originally created as NOT NULL but the model always intended it to
be optional — not every provider exposes a display name.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "c3d4e5f6a7b8"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "provider_vehicle_categories",
        "external_name",
        existing_type=sa.Text(),
        nullable=True,
    )


def downgrade() -> None:
    # Restore NOT NULL: replace any NULLs with empty string first so the
    # constraint is satisfiable without losing rows.
    op.execute(
        "UPDATE provider_vehicle_categories "
        "SET external_name = '' WHERE external_name IS NULL"
    )
    op.alter_column(
        "provider_vehicle_categories",
        "external_name",
        existing_type=sa.Text(),
        nullable=False,
    )
