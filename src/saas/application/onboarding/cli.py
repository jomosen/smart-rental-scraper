"""Tenant onboarding CLI entry point.

Usage:
    python -m src.saas.application.onboarding \\
        --config path/to/tenant.yaml \\
        [--providers-json path/to/providers.json]

The script runs eight steps in sequence. Steps 4-7 share a single transaction
so that partial tenant state is never committed mid-sequence. If any step after
tenant creation fails, the partial tenant is rolled back so the operator can
fix the error and re-run cleanly.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError

from ...infrastructure.persistence.engine import app_engine, super_engine
from ...infrastructure.persistence.session import (
    make_session_factory,
    super_session,
    tenant_context,
)
from .. import discovery
from .config import ConfigError, load_config
from .rollback import rollback_tenant
from .steps import (
    OnboardingError,
    step_activate_subscriptions,
    step_create_mappings,
    step_create_subscriptions,
    step_create_tenant,
    step_create_users_and_groups,
    step_discovery,
    step_validate_catalog,
)

_PROVIDERS_JSON_DEFAULT = Path(__file__).parents[5] / "providers.json"

_DISCOVERY_PERIOD_DAYS = 90
_PICKUP_HOUR = 10


def main(argv=None) -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="python -m src.saas.application.onboarding",
        description="Onboard a new tenant into smart-rental-saas.",
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        metavar="FILE",
        help="Path to tenant onboarding YAML config file.",
    )
    parser.add_argument(
        "--providers-json",
        type=Path,
        default=_PROVIDERS_JSON_DEFAULT,
        metavar="FILE",
        help="Path to providers.json (default: repo-root providers.json).",
    )
    args = parser.parse_args(argv)

    # ── Load config ───────────────────────────────────────────────────────
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"[error] Config file not found: {args.config}", file=sys.stderr)
        return 1
    except ConfigError as e:
        print(f"[error] Config: {e}", file=sys.stderr)
        return 1

    # ── Load providers.json ───────────────────────────────────────────────
    try:
        with open(args.providers_json, encoding="utf-8") as f:
            providers_json = json.load(f)
    except FileNotFoundError:
        print(
            f"[error] providers.json not found at {args.providers_json}. "
            "Pass --providers-json or run from the repo root.",
            file=sys.stderr,
        )
        return 1

    # ── Engine + session factories ────────────────────────────────────────
    _super = super_engine()
    _app = app_engine()
    session_factory = partial(super_session, _super)
    app_factory = make_session_factory(_app)

    # ── Build scraper factory (cross-boundary; contained in discovery.py) ─
    try:
        scraper_factory = discovery.build_scraper_factory(providers_json)
    except ValueError as e:
        print(f"[error] Cannot build scraper factory: {e}", file=sys.stderr)
        return 1

    tenant_id: Optional[uuid.UUID] = None

    # ── Step 1 — validate catalog ─────────────────────────────────────────
    print(f"[1/8] Validating catalog for {len(config.subscriptions)} subscription(s)...")
    try:
        with super_session(_super) as s:
            catalog_ids = step_validate_catalog(config, s)
    except OnboardingError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1

    # ── Step 2 — create tenant ────────────────────────────────────────────
    print(f"[2/8] Creating tenant '{config.tenant.name}'...")
    try:
        with super_session(_super) as s:
            tenant_id = step_create_tenant(config, s)
    except OnboardingError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1
    except IntegrityError as e:
        orig = str(e.orig).lower()
        if "unique" in orig and "tenants" in orig:
            print(
                f"[error] A tenant named '{config.tenant.name}' already exists in the database. "
                "Use a different name or clean up the previous run.",
                file=sys.stderr,
            )
        else:
            print(
                f"[error] Database constraint violation creating tenant '{config.tenant.name}': {e.orig}",
                file=sys.stderr,
            )
        return 1
    except Exception as e:
        print(f"[error] Could not create tenant: {e}", file=sys.stderr)
        return 1

    # ── Step 3 — discovery ────────────────────────────────────────────────
    print(f"[3/8] Running discovery for {len(catalog_ids)} provider tuple(s)...")
    try:
        period_start = datetime.now().replace(
            hour=_PICKUP_HOUR, minute=0, second=0, microsecond=0
        )
        period_end = period_start + timedelta(days=_DISCOVERY_PERIOD_DAYS)
        step_discovery(
            catalog_ids=catalog_ids,
            providers_json=providers_json,
            session_factory=session_factory,
            period_start=period_start,
            period_end=period_end,
            scraper_factory=scraper_factory,
            pickup_hour=_PICKUP_HOUR,
        )
    except OnboardingError as e:
        print(f"[error] Discovery: {e}", file=sys.stderr)
        _rollback(tenant_id, _super)
        return 1
    except Exception as e:
        print(f"[error] Discovery failed unexpectedly: {e}", file=sys.stderr)
        _rollback(tenant_id, _super)
        return 1

    # ── Steps 4-7 — owner, groups, mappings, subscriptions, activation ───
    # All four steps share one transaction so no partial state is ever committed.
    print(
        f"[4-7/8] Creating owner, {len(config.vehicle_groups)} vehicle group(s), "
        "mappings, subscriptions, and activating..."
    )
    try:
        with tenant_context(app_factory, tenant_id) as s:
            client_group_ids = step_create_users_and_groups(config, tenant_id, s)
            mapping_count = step_create_mappings(
                config, catalog_ids, tenant_id, client_group_ids, s
            )
            subscription_ids_by_tuple = step_create_subscriptions(
                config, catalog_ids, tenant_id, s
            )
            final_statuses = step_activate_subscriptions(
                subscription_ids_by_tuple, catalog_ids, tenant_id, s
            )
    except OnboardingError as e:
        print(f"[error] {e}", file=sys.stderr)
        _rollback(tenant_id, _super)
        return 1
    except Exception as e:
        print(f"[error] Failed during tenant setup (steps 4-7): {e}", file=sys.stderr)
        _rollback(tenant_id, _super)
        return 1

    # ── Step 8 — report ───────────────────────────────────────────────────
    active_count = sum(1 for s in final_statuses.values() if s == "active")
    pending_count = sum(1 for s in final_statuses.values() if s == "pending_mapping")

    print("[8/8] Done.\n")
    print("Tenant onboarded successfully:")
    print(f"  tenant_id      : {tenant_id}")
    print(f"  name           : {config.tenant.name}")
    print(f"  owner          : {config.tenant.owner_email}")
    print(f"  vehicle groups : {len(client_group_ids)}")
    print(f"  mappings       : {mapping_count}")
    print(f"  subscriptions  : {len(final_statuses)} total  "
          f"({active_count} active, {pending_count} pending_mapping)")
    if pending_count:
        print(
            "\n[warning] Some subscriptions are still pending_mapping. "
            "Add the missing vehicle group mappings and re-run activation.",
            file=sys.stderr,
        )
    return 0


def _rollback(tenant_id: Optional[uuid.UUID], engine) -> None:
    if tenant_id is None:
        return
    print(
        f"[rollback] Removing partial tenant {tenant_id} — "
        "fix the error above and re-run onboarding.",
        file=sys.stderr,
    )
    try:
        with super_session(engine) as s:
            rollback_tenant(tenant_id, s)
        print("[rollback] Done.", file=sys.stderr)
    except Exception as e:
        print(
            f"[rollback] FAILED for tenant_id={tenant_id} — manual cleanup required: {e}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    sys.exit(main())
