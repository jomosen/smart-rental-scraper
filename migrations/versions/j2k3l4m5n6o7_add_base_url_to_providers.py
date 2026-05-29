"""Add base_url column to providers.

providers.base_url stores the entry URL the scraper navigates to on startup.
For recipe-based providers (e.g. centauro) the URL is also embedded in the
recipe; the column is the canonical runtime source for all scraper types.

The column is TEXT NOT NULL DEFAULT '' so existing rows get an empty string.
Only centauro's base_url is set here (it's already in committed source via
experiments/scraper_builder/run_build_recipe.py).  Other providers must be
updated manually:
    UPDATE providers SET base_url = 'https://...' WHERE code = '<code>';

Revision ID: j2k3l4m5n6o7
Revises: i1j2k3l4m5n6
"""
from alembic import op

revision = "j2k3l4m5n6o7"
down_revision = "i1j2k3l4m5n6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE providers
            ADD COLUMN base_url TEXT NOT NULL DEFAULT ''
    """)

    # centauro's URL is already public in committed source (run_build_recipe.py)
    op.execute("""
        UPDATE providers
           SET base_url = 'https://www.centauro.net'
         WHERE code = 'centauro'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE providers DROP COLUMN IF EXISTS base_url")
