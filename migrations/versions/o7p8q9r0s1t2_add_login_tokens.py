"""Add login_tokens table (homegrown magic-link auth).

Pre-auth table: NO row-level security. When a magic link is requested or
verified there is no tenant context yet, so RLS cannot apply. The table is
accessed ONLY by the auth service, which connects as smart_rental_super
(inherits the schema owner + BYPASSRLS). The runtime app role
(smart_rental_app) is intentionally granted nothing here.

Stores only SHA-256(token) — never the raw token. Tokens are single-use
(used_at) and time-boxed (expires_at). The email + request_ip + created_at
columns back the per-email / per-IP rate limiting.

Revision ID: o7p8q9r0s1t2
Revises: n6o7p8q9r0s1
"""
from alembic import op

revision = "o7p8q9r0s1t2"
down_revision = "n6o7p8q9r0s1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE login_tokens (
            id          UUID         NOT NULL DEFAULT gen_random_uuid(),
            email       TEXT         NOT NULL,
            user_id     UUID         NULL,
            token_hash  CHAR(64)     NOT NULL,
            request_ip  TEXT         NULL,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            expires_at  TIMESTAMPTZ  NOT NULL,
            used_at     TIMESTAMPTZ  NULL,
            CONSTRAINT pk_login_tokens PRIMARY KEY (id),
            CONSTRAINT uq_login_tokens_token_hash UNIQUE (token_hash),
            CONSTRAINT fk_login_tokens_user
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    # Rate-limit windows: count recent requests per email / per IP.
    op.execute("""
        CREATE INDEX ix_login_tokens_email_created
            ON login_tokens (email, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX ix_login_tokens_ip_created
            ON login_tokens (request_ip, created_at DESC)
    """)

    # Deliberately NO RLS and NO grants to smart_rental_app — see module docstring.


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS login_tokens CASCADE")
