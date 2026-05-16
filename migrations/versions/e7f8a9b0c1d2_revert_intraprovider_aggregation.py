"""revert_intraprovider_aggregation

Revision ID: e7f8a9b0c1d2
Revises: c3d4e5f6a7b8
Create Date: 2026-05-16

"""
from alembic import op
import sqlalchemy as sa

revision = "e7f8a9b0c1d2"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the broken partial unique that prevents multiple PVCs from sharing a
    # canonical_type_id within the same (provider, location, rate) tuple.
    op.execute(
        "DROP INDEX IF EXISTS uq_provider_vehicle_categories_canonical"
    )

    # Add attributes_hash for identity when external_code is NULL.
    op.add_column(
        "provider_vehicle_categories",
        sa.Column("attributes_hash", sa.String(16), nullable=True),
    )

    # Identity index when external_code is known.
    op.execute("""
        CREATE UNIQUE INDEX uq_pvc_by_external_code
            ON provider_vehicle_categories
            (provider_id, provider_location_id, provider_rate_id, external_code)
            WHERE external_code IS NOT NULL
    """)

    # Identity index when external_code is absent (hash-based fallback).
    op.execute("""
        CREATE UNIQUE INDEX uq_pvc_by_attributes_hash
            ON provider_vehicle_categories
            (provider_id, provider_location_id, provider_rate_id, attributes_hash)
            WHERE external_code IS NULL AND attributes_hash IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_pvc_by_external_code")
    op.execute("DROP INDEX IF EXISTS uq_pvc_by_attributes_hash")
    op.drop_column("provider_vehicle_categories", "attributes_hash")
    op.execute("""
        CREATE UNIQUE INDEX uq_provider_vehicle_categories_canonical
            ON provider_vehicle_categories
            (provider_id, provider_location_id, provider_rate_id, canonical_type_id)
            WHERE canonical_type_id IS NOT NULL
    """)
