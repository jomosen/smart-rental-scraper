"""Add acriss_full to model_classifications (commercial-group split).

/api/v1/classify now returns two representations per model: `acriss_code`
(the recommended, materialized code — what a mapping should target) and
`acriss_full` (the engine's exact letters, possibly unmaterialized, e.g.
IGAV for an explicit-petrol query). The commercial group (`acriss_group`,
first three letters + wildcard; full code for BEVs) is derived at read time
and needs no column. Cached rows must carry the full code so cache hits can
serve it without re-running the engine.

Revision ID: v4w5x6y7z8a9
Revises: u3v4w5x6y7z8
"""
from alembic import op

revision = "v4w5x6y7z8a9"
down_revision = "u3v4w5x6y7z8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE model_classifications ADD COLUMN acriss_full VARCHAR(4) NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE model_classifications DROP COLUMN IF EXISTS acriss_full"
    )
