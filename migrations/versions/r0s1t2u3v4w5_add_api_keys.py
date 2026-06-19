"""Add api_keys table (machine-to-machine access for the public prices API).

A tenant-scoped table holding **hashed** API keys. The raw key is shown once at
creation and never stored. Lookup on each request hashes the presented bearer
token and matches it here; because the request has no tenant context yet, that
lookup runs as smart_rental_super (BYPASSRLS). Management (create/revoke) also
runs as super via scripts/create_api_key.py.

RLS mirrors users: app DML is granted and FORCE RLS + tenant_isolation policy is
applied, so any app-role access without app.tenant_id sees zero rows (fail-safe).

- key_hash: sha256 hex of the raw key (high-entropy token, fast hash is fine).
- key_prefix: leading chars of the raw key, for display/identification only.
- revoked_at: NULL = active; set to revoke without deleting the audit row.

Revision ID: r0s1t2u3v4w5
Revises: q9r0s1t2u3v4
"""
from alembic import op

revision = "r0s1t2u3v4w5"
down_revision = "q9r0s1t2u3v4"
branch_labels = None
depends_on = None

_APP_USER = "smart_rental_app"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE api_keys (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            tenant_id   UUID        NOT NULL,
            name        TEXT        NOT NULL,
            key_prefix  VARCHAR(16) NOT NULL,
            key_hash    TEXT        NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_used_at TIMESTAMPTZ NULL,
            revoked_at  TIMESTAMPTZ NULL,
            CONSTRAINT pk_api_keys PRIMARY KEY (id),
            CONSTRAINT fk_api_keys_tenant FOREIGN KEY (tenant_id)
                REFERENCES tenants (id) ON DELETE RESTRICT
        )
    """)
    # Global unique index: a key hash resolves to exactly one tenant with no
    # tenant context, so it must be unique across all tenants.
    op.execute("CREATE UNIQUE INDEX uq_api_keys_key_hash ON api_keys (key_hash)")
    op.execute("CREATE INDEX ix_api_keys_tenant ON api_keys (tenant_id)")

    # RLS mirror of users (see 80486ab5bff3._rls).
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON api_keys TO {_APP_USER}")
    op.execute("ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE api_keys FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON api_keys
            USING      (tenant_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS api_keys")
