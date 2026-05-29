"""Rename victoria_rent_a_car → victoria.

Revision ID: l4m5n6o7p8q9
Revises: k3l4m5n6o7p8
"""
from alembic import op

revision = "l4m5n6o7p8q9"
down_revision = "k3l4m5n6o7p8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE providers
           SET code        = 'victoria',
               scraper_key = 'victoria'
         WHERE code = 'victoria_rent_a_car'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE providers
           SET code        = 'victoria_rent_a_car',
               scraper_key = 'victoria_rent_a_car'
         WHERE code = 'victoria'
    """)
