"""add family to canonical_vehicle_types

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-05-17

Adds the `family` column to canonical_vehicle_types so that the DB can
answer queries like "price range of family X" across all canonical categories
that share the same semantic tier within a body type.

family is structural metadata for the system; it is NOT passed to the LLM
classifier — the LLM classifies by code, description, criteria, and examples.
"""
from alembic import op

revision = "f8a9b0c1d2e3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOT NULL DEFAULT '' lets existing v1 rows keep an empty family until the
    # seed script re-runs (which sets the real value for every active category).
    op.execute("""
        ALTER TABLE canonical_vehicle_types
        ADD COLUMN family VARCHAR(64) NOT NULL DEFAULT ''
    """)
    op.execute("""
        CREATE INDEX ix_canonical_vehicle_types_family
            ON canonical_vehicle_types (family)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_canonical_vehicle_types_family")
    op.execute(
        "ALTER TABLE canonical_vehicle_types DROP COLUMN family"
    )
