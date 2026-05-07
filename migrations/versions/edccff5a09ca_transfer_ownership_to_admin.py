"""transfer ownership to admin

Revision ID: edccff5a09ca
Revises: 80486ab5bff3
Create Date: 2026-05-07 17:05:46.090366

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'edccff5a09ca'
down_revision: Union[str, Sequence[str], None] = '80486ab5bff3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ADMIN = 'smart_rental_admin'
_APP = 'smart_rental_app'

# Catalog tables (Hito 1) — need ownership transfer AND app GRANTs.
# Tenant tables (Hito 2) already received per-table GRANTs via _rls(); they
# still need the ownership transfer.
_CATALOG_TABLES = [
    'providers',
    'provider_locations',
    'provider_rates',
    'provider_vehicle_groups',
    'scrape_runs',
    'homogeneous_zones',
    'price_observation_heartbeats',
]

_TENANT_TABLES = [
    'tenants',
    'users',
    'client_vehicle_groups',
    'vehicle_group_mappings',
    'tenant_subscriptions',
    'pricing_rules',
    'pricing_outputs',
]


def upgrade() -> None:
    # --- Ownership: all tables (including price_observations parent) ----------
    for table in _CATALOG_TABLES + ['price_observations'] + _TENANT_TABLES:
        op.execute(f'ALTER TABLE {table} OWNER TO {_ADMIN}')

    # price_observations partitions are discovered dynamically so this
    # migration stays correct regardless of when it was applied.
    op.execute(f"""
        DO $$
        DECLARE
            part TEXT;
        BEGIN
            FOR part IN
                SELECT inhrelid::regclass::text
                FROM   pg_inherits
                WHERE  inhparent = 'price_observations'::regclass
            LOOP
                EXECUTE format('ALTER TABLE %I OWNER TO {_ADMIN}', part);
            END LOOP;
        END
        $$
    """)

    # --- Catalog GRANTs for smart_rental_app ---------------------------------
    # Tenant-table GRANTs were applied inside the RLS migration; only the
    # catalog tables (no RLS) are missing them.
    for table in _CATALOG_TABLES + ['price_observations']:
        op.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_APP}'
        )

    # --- Sequence GRANTs -----------------------------------------------------
    # Covers every BIGINT GENERATED … AS IDENTITY column in all Hito-1 tables.
    # USAGE lets nextval() work; SELECT lets currval() work.
    op.execute(
        f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP}'
    )


def downgrade() -> None:
    # Reverting table ownership to the bootstrap superuser is intentionally
    # skipped — it requires knowing the original owner and is rarely needed
    # in practice. Revoke the GRANTs that this migration added.
    op.execute(
        f'REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM {_APP}'
    )
    for table in _CATALOG_TABLES + ['price_observations']:
        op.execute(
            f'REVOKE SELECT, INSERT, UPDATE, DELETE ON {table} FROM {_APP}'
        )
