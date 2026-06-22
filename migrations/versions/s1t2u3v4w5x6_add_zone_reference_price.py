"""Add reference_price to homogeneous_zones.

The SeasonAnalyzer already computes a per-zone reference price (guide-group
price/day that defined the zone); it was dropped at persistence. Persist it so:
  - the scrape can carry forward a season's start_date when the leading zone is
    price-continuous with the previously-active one (stable season identity —
    see DATA_MODEL homogeneous_zones note), and
  - a zone is self-describing for history.

Nullable: pre-existing rows keep NULL; populated from the next scrape onward.

Revision ID: s1t2u3v4w5x6
Revises: r0s1t2u3v4w5
"""
from alembic import op

revision = "s1t2u3v4w5x6"
down_revision = "r0s1t2u3v4w5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE homogeneous_zones ADD COLUMN reference_price NUMERIC(10,2) NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE homogeneous_zones DROP COLUMN IF EXISTS reference_price")
