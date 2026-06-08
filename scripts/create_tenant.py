"""Provision a tenant together with its owner user (email + password).

Back-office / provisioning operation: runs as smart_rental_super (BYPASSRLS),
because it crosses tenant scope and writes the tenant + user rows.

Usage:
    # Create a new tenant and its owner, with an explicit password:
    python scripts/create_tenant.py --name "Acme Rentals" \\
        --email owner@acme.com --password "s3cret!" [--currency EUR] [--plan mvp]

    # Let the script generate a strong password and print it once:
    python scripts/create_tenant.py --name "Acme Rentals" --email owner@acme.com

    # Attach the owner to an existing tenant instead of creating a new one:
    python scripts/create_tenant.py --tenant-id <uuid> --email owner@acme.com --password ...

Email is globally unique (case-insensitive). If the email already exists the
script aborts unless --reset-password is given, in which case only the password
(and session_version bump) is applied to that existing user.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from src.saas.infrastructure.auth.security import hash_password
from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.session import super_session


def _gen_password() -> str:
    # 24 url-safe chars ≈ 142 bits; comfortably under the 72-byte bcrypt limit.
    return secrets.token_urlsafe(18)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Provision a tenant + owner user.")
    parser.add_argument("--name", help="Tenant display name (omit only with --tenant-id).")
    parser.add_argument("--tenant-id", default=None, help="Attach owner to this existing tenant.")
    parser.add_argument("--email", required=True, help="Owner email (login identity, globally unique).")
    parser.add_argument("--password", default=None, help="Owner password (generated if omitted).")
    parser.add_argument("--currency", default="EUR", help="ISO 4217 currency (default EUR).")
    parser.add_argument("--plan", default="mvp", help="Tenant plan (default mvp).")
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="If the email already exists, reset its password instead of aborting.",
    )
    args = parser.parse_args(argv)

    if not args.name and not args.tenant_id:
        print("ERROR: provide --name to create a tenant, or --tenant-id to attach to one.", file=sys.stderr)
        return 1

    email = args.email.strip().lower()
    raw_password = args.password or _gen_password()
    generated = args.password is None
    if len(raw_password.encode("utf-8")) > 72:
        print("ERROR: password exceeds the 72-byte bcrypt limit.", file=sys.stderr)
        return 1
    pw_hash = hash_password(raw_password)

    with super_session(super_engine()) as s:
        existing = s.execute(
            text("SELECT id, tenant_id FROM users WHERE LOWER(email) = :e"),
            {"e": email},
        ).fetchone()

        if existing is not None:
            if not args.reset_password:
                print(
                    f"ERROR: a user with email '{email}' already exists "
                    f"(tenant_id={existing.tenant_id}). Use --reset-password to set a new password.",
                    file=sys.stderr,
                )
                return 1
            s.execute(
                text("""
                    UPDATE users
                    SET password_hash = :h, session_version = session_version + 1
                    WHERE id = :id
                """),
                {"h": pw_hash, "id": existing.id},
            )
            _report(email, raw_password if generated else None, existing.tenant_id, existing.id, reset=True)
            return 0

        # Resolve / create the tenant.
        if args.tenant_id:
            tenant = s.execute(
                text("SELECT id, name FROM tenants WHERE id = :id"),
                {"id": args.tenant_id},
            ).fetchone()
            if tenant is None:
                print(f"ERROR: tenant {args.tenant_id} not found.", file=sys.stderr)
                return 1
            tenant_id, tenant_name = tenant.id, tenant.name
        else:
            row = s.execute(
                text("""
                    INSERT INTO tenants (name, currency, plan)
                    VALUES (:name, :currency, :plan)
                    RETURNING id
                """),
                {"name": args.name, "currency": args.currency.upper(), "plan": args.plan},
            ).fetchone()
            tenant_id, tenant_name = row.id, args.name

        user = s.execute(
            text("""
                INSERT INTO users (tenant_id, email, password_hash, role)
                VALUES (:tenant_id, :email, :h, 'owner')
                RETURNING id
            """),
            {"tenant_id": tenant_id, "email": email, "h": pw_hash},
        ).fetchone()

    _report(email, raw_password if generated else None, tenant_id, user.id, tenant_name=tenant_name)
    return 0


def _report(email, generated_password, tenant_id, user_id, tenant_name=None, reset=False):
    if reset:
        print(f"Password reset for {email} (tenant_id={tenant_id}, user_id={user_id}).")
    else:
        print("Tenant provisioned:")
        print(f"  tenant_id : {tenant_id}")
        if tenant_name:
            print(f"  name      : {tenant_name}")
        print(f"  owner     : {email} (user_id={user_id})")
    if generated_password is not None:
        print(f"\n  GENERATED PASSWORD (shown once): {generated_password}\n")


if __name__ == "__main__":
    sys.exit(main())
