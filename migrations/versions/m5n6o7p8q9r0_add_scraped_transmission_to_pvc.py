"""Add scraped transmission column to provider_vehicle_categories.

Revision ID: m5n6o7p8q9r0
Revises: l4m5n6o7p8q9
"""
import sqlalchemy as sa
from alembic import op

revision = "m5n6o7p8q9r0"
down_revision = "l4m5n6o7p8q9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provider_vehicle_categories",
        sa.Column("transmission", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("provider_vehicle_categories", "transmission")
