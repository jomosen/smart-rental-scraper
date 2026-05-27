"""Add provider_recipes table.

Catalog table (no tenant_id, no RLS).  Stores versioned scraping recipes
for each provider.  Append-versioning: new row per builder run, previous
active row flipped to active=false atomically.  Partial unique index ensures
at most one active recipe per provider at any time.

Revision ID: i1j2k3l4m5n6
Revises: h0i1j2k3l4m5
"""
from alembic import op

revision = "i1j2k3l4m5n6"
down_revision = "h0i1j2k3l4m5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE provider_recipes (
            id            BIGSERIAL    NOT NULL,
            provider_id   BIGINT       NOT NULL,
            version       INT          NOT NULL,
            recipe_jsonb  JSONB        NOT NULL,
            discovered_at TIMESTAMPTZ  NULL,
            active        BOOLEAN      NOT NULL DEFAULT false,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT pk_provider_recipes PRIMARY KEY (id),
            CONSTRAINT fk_provider_recipes_provider
                FOREIGN KEY (provider_id) REFERENCES providers (id)
                ON DELETE RESTRICT
        )
    """)

    # Partial unique index: at most one active recipe per provider
    op.execute("""
        CREATE UNIQUE INDEX uq_provider_recipes_one_active_per_provider
            ON provider_recipes (provider_id)
            WHERE active = true
    """)

    # Non-unique index for history queries ordered by version
    op.execute("""
        CREATE INDEX ix_provider_recipes_provider_version
            ON provider_recipes (provider_id, version DESC)
    """)

    op.execute("""
        GRANT SELECT, INSERT, UPDATE ON provider_recipes TO smart_rental_app
    """)
    op.execute("""
        GRANT USAGE, SELECT ON SEQUENCE provider_recipes_id_seq TO smart_rental_app
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS provider_recipes CASCADE")
