"""Insert a user (email + tenant) so the magic-link login can be tested.

Runs as smart_rental_super (BYPASSRLS) — user creation is a back-office /
provisioning operation that crosses tenant scope.

Usage:
    python scripts/seed_user.py --email info@josemorell.com
    python scripts/seed_user.py --email x@y.com --tenant-id <uuid>

Without --tenant-id it attaches the user to the first tenant in the DB.
Idempotent: re-running with the same (tenant, email) is a no-op.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text

from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.session import super_session


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a user for magic-link login.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--tenant-id", default=None, help="Target tenant UUID (default: first tenant)")
    args = parser.parse_args()

    email = args.email.strip().lower()

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
            print("ERROR: no tenant found. Create a tenant first.")
            return 1

        s.execute(
            text("""
                INSERT INTO users (tenant_id, email, role)
                VALUES (:tenant_id, :email, 'owner')
                ON CONFLICT (tenant_id, email) DO NOTHING
            """),
            {"tenant_id": tenant.id, "email": email},
        )
        row = s.execute(
            text("SELECT id FROM users WHERE tenant_id = :t AND LOWER(email) = :e"),
            {"t": tenant.id, "e": email},
        ).fetchone()

    print(f"User ready: {email} -> tenant '{tenant.name}' ({tenant.id}) user_id={row.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
