"""Create or revoke a tenant API key for the public prices API (/api/v1/prices).

Back-office / provisioning operation: runs as smart_rental_super (BYPASSRLS),
because it crosses tenant scope and writes the api_keys row.

The raw key is printed ONCE on creation and never stored (only its sha256 hash
and a short display prefix are persisted). Treat it like a password.

Usage:
    # Create a key for a tenant (resolved by owner email):
    python scripts/create_api_key.py --email owner@acme.com --name "Booking sync"

    # Or by tenant id:
    python scripts/create_api_key.py --tenant-id <uuid> --name "Booking sync"

    # List a tenant's keys (prefixes only, never the raw key):
    python scripts/create_api_key.py --email owner@acme.com --list

    # Revoke a key by its prefix (shown by --list / on creation):
    python scripts/create_api_key.py --email owner@acme.com --revoke rr_live_AbCd
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from src.saas.infrastructure.auth.security import generate_api_key
from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.session import super_session


def _resolve_tenant_id(s, email: str | None, tenant_id: str | None) -> str | None:
    if tenant_id:
        row = s.execute(text("SELECT id FROM tenants WHERE id = :id"), {"id": tenant_id}).fetchone()
        return str(row.id) if row else None
    row = s.execute(
        text("SELECT tenant_id FROM users WHERE LOWER(email) = :e"),
        {"e": email.strip().lower()},
    ).fetchone()
    return str(row.tenant_id) if row else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Manage tenant API keys for /api/v1/prices.")
    p.add_argument("--email", help="Owner email to resolve the tenant.")
    p.add_argument("--tenant-id", default=None, help="Tenant UUID (instead of --email).")
    p.add_argument("--name", default="api key", help="Human label for the key.")
    p.add_argument("--list", action="store_true", help="List the tenant's keys and exit.")
    p.add_argument("--revoke", default=None, metavar="PREFIX", help="Revoke the key with this prefix.")
    args = p.parse_args(argv)

    if not args.email and not args.tenant_id:
        print("ERROR: provide --email or --tenant-id.", file=sys.stderr)
        return 1

    with super_session(super_engine()) as s:
        tid = _resolve_tenant_id(s, args.email, args.tenant_id)
        if tid is None:
            print("ERROR: tenant not found.", file=sys.stderr)
            return 1

        if args.list:
            rows = s.execute(
                text("""
                    SELECT key_prefix, name, created_at, last_used_at, revoked_at
                    FROM api_keys WHERE tenant_id = :t ORDER BY created_at
                """),
                {"t": tid},
            ).fetchall()
            if not rows:
                print("(no keys)")
            for r in rows:
                state = "REVOKED" if r.revoked_at else "active"
                print(f"  {r.key_prefix}…  [{state}]  {r.name}  created={r.created_at}  last_used={r.last_used_at}")
            return 0

        if args.revoke:
            res = s.execute(
                text("""
                    UPDATE api_keys SET revoked_at = NOW()
                    WHERE tenant_id = :t AND key_prefix = :p AND revoked_at IS NULL
                """),
                {"t": tid, "p": args.revoke},
            )
            print(f"Revoked {res.rowcount} key(s) with prefix '{args.revoke}'.")
            return 0

        # Create
        raw, prefix, key_hash = generate_api_key()
        s.execute(
            text("""
                INSERT INTO api_keys (tenant_id, name, key_prefix, key_hash)
                VALUES (:t, :n, :p, :h)
            """),
            {"t": tid, "n": args.name, "p": prefix, "h": key_hash},
        )

    print("API key created (shown once — store it now):\n")
    print(f"  tenant_id : {tid}")
    print(f"  name      : {args.name}")
    print(f"  prefix    : {prefix}")
    print(f"\n  API KEY   : {raw}\n")
    print("Use it as:  Authorization: Bearer <API KEY>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
