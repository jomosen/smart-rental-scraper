"""create tenant tables with RLS

Revision ID: 80486ab5bff3
Revises: 745519b5af6f
Create Date: 2026-05-07 13:50:24.870529

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = '80486ab5bff3'
down_revision: Union[str, Sequence[str], None] = '745519b5af6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APP_USER = 'smart_rental_app'
_UUID_DEFAULT = sa.text('gen_random_uuid()')


def _rls(table: str, tenant_col: str = 'tenant_id') -> None:
    """Grant DML to the app user, enable RLS with FORCE, create isolation policy.

    tenant_col is 'id' for the tenants table itself (which has no tenant_id),
    and 'tenant_id' for every other multi-tenant table.
    """
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_APP_USER}"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    # USING covers SELECT/UPDATE/DELETE; WITH CHECK covers INSERT/UPDATE.
    # current_setting(..., true) returns NULL (not an error) when app.tenant_id
    # is not set, so unscoped connections see zero rows — fail-safe isolation.
    op.execute(f"""
        CREATE POLICY tenant_isolation ON {table}
            USING      ({tenant_col} = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK ({tenant_col} = current_setting('app.tenant_id', true)::uuid)
    """)


def upgrade() -> None:
    # gen_random_uuid() is built-in from PG13+; pgcrypto also provides it
    # for compatibility with older versions.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ------------------------------------------------------------------ #
    # tenants — root of the multi-tenant hierarchy                        #
    # ------------------------------------------------------------------ #
    op.create_table(
        'tenants',
        sa.Column('id', sa.Uuid(), nullable=False, server_default=_UUID_DEFAULT),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('currency', sa.CHAR(3), nullable=False),
        sa.Column('plan', sa.String(32), nullable=False, server_default='mvp'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
    )
    # Policy for tenants uses 'id' (there is no tenant_id column here).
    _rls('tenants', tenant_col='id')

    # ------------------------------------------------------------------ #
    # users                                                               #
    # ------------------------------------------------------------------ #
    op.create_table(
        'users',
        sa.Column('id', sa.Uuid(), nullable=False, server_default=_UUID_DEFAULT),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        # external_auth_id: nullable until an auth provider is wired up.
        # All production users will have this once auth is integrated.
        sa.Column('external_auth_id', sa.String(255), nullable=True),
        sa.Column('role', sa.String(32), nullable=False, server_default='owner'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.CheckConstraint("role IN ('owner')", name='ck_users_role'),
        sa.ForeignKeyConstraint(
            ['tenant_id'], ['tenants.id'],
            name='fk_users_tenant', ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'email', name='uq_users_tenant_email'),
    )
    _rls('users')

    # ------------------------------------------------------------------ #
    # client_vehicle_groups                                               #
    # ------------------------------------------------------------------ #
    op.create_table(
        'client_vehicle_groups',
        sa.Column('id', sa.Uuid(), nullable=False, server_default=_UUID_DEFAULT),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(32), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(
            ['tenant_id'], ['tenants.id'],
            name='fk_client_vehicle_groups_tenant', ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'code',
            name='uq_client_vehicle_groups_tenant_code',
        ),
    )
    _rls('client_vehicle_groups')

    # ------------------------------------------------------------------ #
    # vehicle_group_mappings                                              #
    # ------------------------------------------------------------------ #
    op.create_table(
        'vehicle_group_mappings',
        sa.Column('id', sa.Uuid(), nullable=False, server_default=_UUID_DEFAULT),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('client_vehicle_group_id', sa.Uuid(), nullable=False),
        sa.Column('provider_vehicle_group_id', sa.BigInteger(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ['tenant_id'], ['tenants.id'],
            name='fk_vehicle_group_mappings_tenant', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['client_vehicle_group_id'], ['client_vehicle_groups.id'],
            name='fk_vehicle_group_mappings_client_group', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['provider_vehicle_group_id'], ['provider_vehicle_groups.id'],
            name='fk_vehicle_group_mappings_provider_group', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['created_by'], ['users.id'],
            name='fk_vehicle_group_mappings_created_by', ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'client_vehicle_group_id', 'provider_vehicle_group_id',
            name='uq_vehicle_group_mappings_tuple',
        ),
    )
    _rls('vehicle_group_mappings')

    # ------------------------------------------------------------------ #
    # tenant_subscriptions                                                #
    # ------------------------------------------------------------------ #
    op.create_table(
        'tenant_subscriptions',
        sa.Column('id', sa.Uuid(), nullable=False, server_default=_UUID_DEFAULT),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('provider_id', sa.BigInteger(), nullable=False),
        sa.Column('provider_location_id', sa.BigInteger(), nullable=False),
        sa.Column('provider_rate_id', sa.BigInteger(), nullable=False),
        sa.Column('period_days', sa.Integer(), nullable=False, server_default='90'),
        sa.Column('status', sa.String(32), nullable=False,
                  server_default='pending_discovery'),
        sa.Column('subscribed_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.Column('cancelled_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending_discovery', 'pending_mapping', 'active',"
            " 'paused', 'cancelled', 'broken')",
            name='ck_tenant_subscriptions_status',
        ),
        sa.ForeignKeyConstraint(
            ['tenant_id'], ['tenants.id'],
            name='fk_tenant_subscriptions_tenant', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['provider_id'], ['providers.id'],
            name='fk_tenant_subscriptions_provider', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['provider_location_id'], ['provider_locations.id'],
            name='fk_tenant_subscriptions_location', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['provider_rate_id'], ['provider_rates.id'],
            name='fk_tenant_subscriptions_rate', ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    # Partial unique index: at most one active|paused subscription per tuple.
    # Uses IN() in WHERE — requires raw DDL, op.create_index() can't express it.
    op.execute("""
        CREATE UNIQUE INDEX ix_tenant_subscriptions_active_unique
            ON tenant_subscriptions
            (tenant_id, provider_id, provider_location_id, provider_rate_id)
            WHERE status IN ('active', 'paused')
    """)
    _rls('tenant_subscriptions')

    # ------------------------------------------------------------------ #
    # pricing_rules (versioned; self-referential for supersession chain) #
    # ------------------------------------------------------------------ #
    op.create_table(
        'pricing_rules',
        sa.Column('id', sa.Uuid(), nullable=False, server_default=_UUID_DEFAULT),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('client_vehicle_group_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('condition_jsonb', postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column('formula_jsonb', postgresql.JSONB(), nullable=False),
        sa.Column('floor', sa.Numeric(10, 2), nullable=True),
        sa.Column('ceiling', sa.Numeric(10, 2), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.Column('superseded_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('superseded_by_id', sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            'floor IS NULL OR ceiling IS NULL OR floor <= ceiling',
            name='ck_pricing_rules_floor_ceiling',
        ),
        sa.ForeignKeyConstraint(
            ['tenant_id'], ['tenants.id'],
            name='fk_pricing_rules_tenant', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['client_vehicle_group_id'], ['client_vehicle_groups.id'],
            name='fk_pricing_rules_client_group', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['superseded_by_id'], ['pricing_rules.id'],
            name='fk_pricing_rules_superseded_by', ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    _rls('pricing_rules')

    # ------------------------------------------------------------------ #
    # pricing_outputs                                                     #
    # ------------------------------------------------------------------ #
    op.create_table(
        'pricing_outputs',
        sa.Column('id', sa.Uuid(), nullable=False, server_default=_UUID_DEFAULT),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('client_vehicle_group_id', sa.Uuid(), nullable=False),
        sa.Column('pickup_date', sa.Date(), nullable=False),
        sa.Column('duration_days', sa.Integer(), nullable=False),
        sa.Column('computed_price', sa.Numeric(10, 2), nullable=False),
        sa.Column('rule_id', sa.Uuid(), nullable=False),
        sa.Column('inputs_snapshot_jsonb', postgresql.JSONB(), nullable=False),
        sa.Column('computed_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(
            ['tenant_id'], ['tenants.id'],
            name='fk_pricing_outputs_tenant', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['client_vehicle_group_id'], ['client_vehicle_groups.id'],
            name='fk_pricing_outputs_client_group', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['rule_id'], ['pricing_rules.id'],
            name='fk_pricing_outputs_rule', ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    # DESC on computed_at requires raw DDL.
    op.execute("""
        CREATE INDEX ix_pricing_outputs_lookup
            ON pricing_outputs
            (tenant_id, client_vehicle_group_id, pickup_date,
             duration_days, computed_at DESC)
    """)
    _rls('pricing_outputs')


def downgrade() -> None:
    # Drop in reverse FK dependency order.
    # DROP TABLE cascades to: indexes, RLS policies, and grants on the table.
    op.drop_table('pricing_outputs')
    op.drop_table('pricing_rules')
    op.drop_table('tenant_subscriptions')
    op.drop_table('vehicle_group_mappings')
    op.drop_table('client_vehicle_groups')
    op.drop_table('users')
    op.drop_table('tenants')
    # pgcrypto is intentionally left in place — it may be used elsewhere
    # and its presence does not affect schema state after the tables are gone.
