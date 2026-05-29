"""Rename provider_b → victoria_rent_a_car, provider_c → solcar.

Updates providers.code, providers.scraper_key, and providers.base_url for the
two custom scrapers that previously used opaque keys.

Revision ID: k3l4m5n6o7p8
Revises: j2k3l4m5n6o7
"""
from alembic import op

revision = "k3l4m5n6o7p8"
down_revision = "j2k3l4m5n6o7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE providers
           SET code        = 'victoria_rent_a_car',
               scraper_key = 'victoria_rent_a_car',
               base_url    = 'https://bookings.victoriacars.com/?idioma=es'
         WHERE code = 'provider_b'
    """)
    op.execute("""
        UPDATE providers
           SET code        = 'solcar',
               scraper_key = 'solcar',
               base_url    = 'https://solcar.es/funnel/'
         WHERE code = 'provider_c'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE providers
           SET code        = 'provider_b',
               scraper_key = 'provider_b',
               base_url    = ''
         WHERE code = 'victoria_rent_a_car'
    """)
    op.execute("""
        UPDATE providers
           SET code        = 'provider_c',
               scraper_key = 'provider_c',
               base_url    = ''
         WHERE code = 'solcar'
    """)
