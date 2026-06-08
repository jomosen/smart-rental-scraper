"""Insert (or update the password of) an owner user so email+password login works.

Runs as smart_rental_super (BYPASSRLS) — user creation is a back-office /
provisioning operation that crosses tenant scope.

For provisioning a brand-new tenant together with its owner, prefer
`scripts/create_tenant.py`. This script is the lighter "attach/seed a user on an
existing tenant (and set its password)" helper.

Usage:
    python scripts/seed_user.py --email info@josemorell.com --password "s3cret!"
    python scripts/seed_user.py --email x@y.com --tenant-id <uuid> --password ...

Without --tenant-id it attaches the user to the first tenant in the DB.
Idempotent on (tenant, email): re-running updates the password and bumps
session_version (invalidating that user's existing sessions).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text

from src.saas.infrastructure.auth.security import hash_password
from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.session import super_session


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed/refresh an owner user for password login.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True, help="Password to set for this user.")
    parser.add_argument("--tenant-id", default=None, help="Target tenant UUID (default: first tenant)")
    args = parser.parse_args()

    email = args.email.strip().lower()
    if len(args.password.encode("utf-8")) > 72:
        print("ERROR: password exceeds the 72-byte bcrypt limit.")
        return 1
    pw_hash = hash_password(args.password)

    with super_session(super_engine()) as s:
        if args.tenant_id:
            tenant = s.execute(
                text("SELECT id, name FROM tenants WHERE id = :id"),
                {"id": args.tenant_id},
            ).fetchone()
        else:
            tenant = s.execute(
                text("SELECT id, name FROM tenants ORDER BY created_at LIMIT 1")
            ).fetchone()

        if tenant is None:
            print("ERROR: no tenant found. Create a tenant first (scripts/create_tenant.py).")
            return 1

        s.execute(
            text("""
                INSERT INTO users (tenant_id, email, password_hash, role)
                VALUES (:tenant_id, :email, :h, 'owner')
                ON CONFLICT (tenant_id, email)
                DO UPDATE SET password_hash = EXCLUDED.password_hash,
                              session_version = users.session_version + 1
            """),
            {"tenant_id": tenant.id, "email": email, "h": pw_hash},
        )
        row = s.execute(
            text("SELECT id FROM users WHERE tenant_id = :t AND LOWER(email) = :e"),
            {"t": tenant.id, "e": email},
        ).fetchone()

    print(f"User ready: {email} -> tenant '{tenant.name}' ({tenant.id}) user_id={row.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
