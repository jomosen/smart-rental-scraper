"""Add acriss_review_queue + provider_vehicle_categories.classification_detail.

Engine v2 (deterministic ACRISS classification — see docs/DATA_MODEL.md
Decision 12):
  - acriss_review_queue: unknown models found during classification, awaiting
    operator validation before being promoted to data/acriss-models.json.
    Catalog scope (no tenant_id, no RLS) — operational state shared between
    scraper runs and back-office review.
  - classification_detail: full engine output per provider group (per-letter
    confidence/source, alternatives, assumptions, explanation).

Revision ID: u3v4w5x6y7z8
Revises: t2u3v4w5x6y7
"""
from alembic import op

revision = "u3v4w5x6y7z8"
down_revision = "t2u3v4w5x6y7"
branch_labels = None
depends_on = None

_APP_USER = "smart_rental_app"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE acriss_review_queue (
            id                   BIGSERIAL    PRIMARY KEY,
            normalized_model     TEXT         NOT NULL,
            raw_model            TEXT         NOT NULL,
            suggested_category   CHAR(1)      NULL,
            suggested_type       CHAR(1)      NULL,
            suggested_powertrain VARCHAR(16)  NULL,
            suggested_acriss     VARCHAR(4)   NULL,
            confidence           NUMERIC(4,3) NULL,
            reason               TEXT         NULL,
            sources_seen         JSONB        NOT NULL DEFAULT '[]',
            status               VARCHAR(16)  NOT NULL DEFAULT 'pending_review',
            first_seen_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            last_seen_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_acriss_review_queue_model UNIQUE (normalized_model),
            CONSTRAINT ck_acriss_review_queue_status
                CHECK (status IN ('pending_review', 'accepted', 'rejected'))
        )
    """)
    # Catalog table: no RLS. The scraper writes via the super role (inherits
    # admin, the owner); the API/back-office reads and updates via the app role.
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON acriss_review_queue TO {_APP_USER}"
    )
    op.execute(
        f"GRANT USAGE ON SEQUENCE acriss_review_queue_id_seq TO {_APP_USER}"
    )

    op.execute("""
        ALTER TABLE provider_vehicle_categories
        ADD COLUMN classification_detail JSONB NULL
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE provider_vehicle_categories DROP COLUMN IF EXISTS classification_detail"
    )
    op.execute("DROP TABLE IF EXISTS acriss_review_queue")
