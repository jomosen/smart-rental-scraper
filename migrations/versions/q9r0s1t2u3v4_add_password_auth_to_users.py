"""Add email + password authentication columns to users.

Replaces the magic-link login flow with a conventional email + password flow
(see docs/DATA_MODEL.md §"Authentication: email + password").

- `password_hash TEXT NULL` — bcrypt hash. NULL = the user cannot log in yet
  (e.g. created by onboarding before a password is assigned). The raw password
  is never stored.
- `session_version INT NOT NULL DEFAULT 0` — carried inside the session JWT.
  Bumping it invalidates every outstanding token for that user (password change,
  log-out-everywhere) without rotating the global JWT_SECRET.
- Global UNIQUE index on LOWER(email): password login resolves an email to a
  single user with no tenant context, so the address must be globally unique.
  The legacy per-tenant unique (tenant_id, email) is kept.

`login_tokens` is intentionally left in place (retained for a future password
reset flow and the login rate-limit window).

Revision ID: q9r0s1t2u3v4
Revises: p8q9r0s1t2u3
"""
from alembic import op

revision = "q9r0s1t2u3v4"
down_revision = "p8q9r0s1t2u3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE users
            ADD COLUMN password_hash   TEXT    NULL,
            ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0
    """)

    # Password login resolves an email → exactly one user without a tenant
    # context, so email must be globally unique (case-insensitive).
    op.execute("""
        CREATE UNIQUE INDEX uq_users_lower_email
            ON users (LOWER(email))
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_users_lower_email")
    op.execute("""
        ALTER TABLE users
            DROP COLUMN IF EXISTS session_version,
            DROP COLUMN IF EXISTS password_hash
    """)
