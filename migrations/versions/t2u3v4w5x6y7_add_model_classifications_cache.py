"""Add model_classifications cache (ad-hoc /api/v1/classify lookups).

Caches the ACRISS classification of a free-text model string so repeated lookups
("Peugeot 208 Manual") don't re-hit the LLM. Catalog scope (no tenant_id, no RLS):
a model classifies the same for everyone.

Keyed by (normalized_model, classifier_version): classifier_version is a hash of
acriss_codes.yaml + the prompt version, so a catalog/prompt change invalidates
stale rows automatically (they simply stop matching the current version).

Revision ID: t2u3v4w5x6y7
Revises: s1t2u3v4w5x6
"""
from alembic import op

revision = "t2u3v4w5x6y7"
down_revision = "s1t2u3v4w5x6"
branch_labels = None
depends_on = None

_APP_USER = "smart_rental_app"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE model_classifications (
            normalized_model   TEXT         NOT NULL,
            classifier_version TEXT         NOT NULL,
            acriss_code        TEXT         NULL,
            confidence         NUMERIC(4,3) NOT NULL DEFAULT 0,
            pending_review     BOOLEAN      NOT NULL DEFAULT false,
            created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            last_used_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            hit_count          INTEGER      NOT NULL DEFAULT 0,
            CONSTRAINT pk_model_classifications
                PRIMARY KEY (normalized_model, classifier_version)
        )
    """)
    # Catalog table: no RLS. The API runtime (app role) does read-through writes.
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON model_classifications TO {_APP_USER}"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS model_classifications")
