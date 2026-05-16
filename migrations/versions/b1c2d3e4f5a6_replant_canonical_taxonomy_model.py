"""replant canonical taxonomy model

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-05-15

Structural replant of the vehicle taxonomy.  The old model used
provider.external_code as the stable identity of a vehicle group.
That assumption broke: several providers no longer expose group codes.

New model (three layers, see docs/DATA_MODEL.md Decision 1):
  canonical_vehicle_types  — operator-curated taxonomy (YAML source)
  provider_vehicle_categories — what each provider offers; identity is
                               (provider, canonical_type_id), not external_code
  tenant_vehicle_groups    — opt-in per-tenant labelling

Migration strategy
------------------
  This migration DELETES ALL DATA from every table that changes schema.
  This is a deliberate operator decision: no production tenants exist yet.
  Downgrade restores the schema only, not the data.

Rename map
----------
  provider_vehicle_groups      → provider_vehicle_categories
  client_vehicle_groups        → tenant_vehicle_groups
  vehicle_group_mappings       → tenant_vehicle_group_mappings

Column renames in dependent tables
-----------------------------------
  *.provider_vehicle_group_id  → *.provider_vehicle_category_id
  vehicle_group_mappings.client_vehicle_group_id → tenant_vehicle_group_id
  vehicle_group_mappings.provider_vehicle_group_id → canonical_type_id
  pricing_rules.client_vehicle_group_id → canonical_type_id
  pricing_outputs.client_vehicle_group_id → canonical_type_id
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ADMIN = 'smart_rental_admin'
_APP = 'smart_rental_app'


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. DELETE existing data — FK-safe order                             #
    # ------------------------------------------------------------------ #
    # Leaf tables first (most constrained), root tables last.
    op.execute("DELETE FROM price_observations")
    op.execute("DELETE FROM price_observation_heartbeats")
    op.execute("DELETE FROM homogeneous_zones")
    op.execute("DELETE FROM scrape_runs")
    op.execute("DELETE FROM pricing_outputs")
    op.execute("DELETE FROM pricing_rules")
    op.execute("DELETE FROM vehicle_group_mappings")
    op.execute("DELETE FROM client_vehicle_groups")
    op.execute("DELETE FROM tenant_subscriptions")
    op.execute("DELETE FROM users")
    op.execute("DELETE FROM tenants")
    op.execute("DELETE FROM provider_vehicle_groups")
    op.execute("DELETE FROM provider_rates")
    op.execute("DELETE FROM provider_locations")
    op.execute("DELETE FROM providers")

    # ------------------------------------------------------------------ #
    # 2. CREATE canonical_vehicle_types                                   #
    # ------------------------------------------------------------------ #
    op.create_table(
        'canonical_vehicle_types',
        sa.Column('id', sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column('code', sa.String(64), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('taxonomy_version', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.Column('deprecated_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_canonical_vehicle_types_code'),
    )
    op.execute(f'ALTER TABLE canonical_vehicle_types OWNER TO {_ADMIN}')
    op.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON canonical_vehicle_types TO {_APP}'
    )
    # GENERATED ALWAYS AS IDENTITY sequence: canonical_vehicle_types_id_seq
    op.execute(
        f'GRANT USAGE, SELECT ON SEQUENCE canonical_vehicle_types_id_seq TO {_APP}'
    )

    # ------------------------------------------------------------------ #
    # 3. RENAME provider_vehicle_groups → provider_vehicle_categories     #
    # ------------------------------------------------------------------ #
    # Drop the uniqueness constraint that depended on external_code being
    # the identity key.  All FK constraints from child tables survive the
    # rename because Postgres tracks them by OID.
    op.drop_constraint(
        'uq_provider_vehicle_groups_tuple', 'provider_vehicle_groups', type_='unique'
    )
    op.rename_table('provider_vehicle_groups', 'provider_vehicle_categories')

    # external_code is now documentary metadata, not part of the identity.
    op.alter_column('provider_vehicle_categories', 'external_code', nullable=True)

    # New classification columns
    op.add_column(
        'provider_vehicle_categories',
        sa.Column('canonical_type_id', sa.Integer(), nullable=True),
    )
    op.add_column(
        'provider_vehicle_categories',
        sa.Column('classification_confidence', sa.Float(precision=53), nullable=True),
    )
    op.add_column(
        'provider_vehicle_categories',
        sa.Column('classification_taxonomy_version', sa.Integer(), nullable=True),
    )
    op.add_column(
        'provider_vehicle_categories',
        sa.Column('pending_review', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
    )
    op.add_column(
        'provider_vehicle_categories',
        sa.Column('fuel_type', sa.String(16), nullable=True),
    )

    # FK → canonical_vehicle_types
    op.create_foreign_key(
        'fk_provider_vehicle_categories_canonical_type',
        'provider_vehicle_categories', 'canonical_vehicle_types',
        ['canonical_type_id'], ['id'],
        ondelete='RESTRICT',
    )

    # Partial unique index: only one row per (provider, location, rate, canonical_type)
    # when canonical_type_id is set.  Unclassified rows (NULL) have no uniqueness
    # constraint by design — they are temporary placeholders pending classification.
    op.execute("""
        CREATE UNIQUE INDEX uq_provider_vehicle_categories_canonical
            ON provider_vehicle_categories
            (provider_id, provider_location_id, provider_rate_id, canonical_type_id)
            WHERE canonical_type_id IS NOT NULL
    """)

    # ------------------------------------------------------------------ #
    # 4. RENAME client_vehicle_groups → tenant_vehicle_groups             #
    # ------------------------------------------------------------------ #
    op.rename_table('client_vehicle_groups', 'tenant_vehicle_groups')

    # ------------------------------------------------------------------ #
    # 5. RENAME vehicle_group_mappings → tenant_vehicle_group_mappings    #
    #    + column renames + FK swap                                       #
    # ------------------------------------------------------------------ #
    op.rename_table('vehicle_group_mappings', 'tenant_vehicle_group_mappings')

    op.alter_column(
        'tenant_vehicle_group_mappings', 'client_vehicle_group_id',
        new_column_name='tenant_vehicle_group_id',
    )
    op.alter_column(
        'tenant_vehicle_group_mappings', 'provider_vehicle_group_id',
        new_column_name='canonical_type_id',
    )

    # The old FK on canonical_type_id now points to provider_vehicle_categories.
    # Drop it and replace with a FK to canonical_vehicle_types.
    op.drop_constraint(
        'fk_vehicle_group_mappings_provider_group',
        'tenant_vehicle_group_mappings',
        type_='foreignkey',
    )
    op.create_foreign_key(
        'fk_tenant_vehicle_group_mappings_canonical_type',
        'tenant_vehicle_group_mappings', 'canonical_vehicle_types',
        ['canonical_type_id'], ['id'],
        ondelete='RESTRICT',
    )

    # Rebuild the uniqueness constraint with updated column names.
    op.drop_constraint(
        'uq_vehicle_group_mappings_tuple',
        'tenant_vehicle_group_mappings',
        type_='unique',
    )
    op.create_unique_constraint(
        'uq_tenant_vehicle_group_mappings_tuple',
        'tenant_vehicle_group_mappings',
        ['tenant_id', 'tenant_vehicle_group_id', 'canonical_type_id'],
    )

    # ------------------------------------------------------------------ #
    # 6. Rename FK columns in catalog observation tables                  #
    # ------------------------------------------------------------------ #

    # homogeneous_zones
    op.execute("DROP INDEX ix_homogeneous_zones_active")
    op.alter_column(
        'homogeneous_zones', 'provider_vehicle_group_id',
        new_column_name='provider_vehicle_category_id',
    )
    op.execute("""
        CREATE INDEX ix_homogeneous_zones_active
            ON homogeneous_zones
            (provider_id, provider_location_id, provider_rate_id,
             provider_vehicle_category_id, start_date, end_date)
            WHERE active = true
    """)

    # price_observations — partitioned table; ALTER propagates to partitions.
    op.execute("DROP INDEX ix_price_observations_main")
    op.execute(
        "ALTER TABLE price_observations "
        "RENAME COLUMN provider_vehicle_group_id TO provider_vehicle_category_id"
    )
    op.execute("""
        CREATE INDEX ix_price_observations_main
            ON price_observations
            (provider_id, provider_location_id, provider_rate_id,
             provider_vehicle_category_id, pickup_date, duration_days,
             observed_at DESC)
    """)

    # price_observation_heartbeats — rename in composite PK column.
    op.alter_column(
        'price_observation_heartbeats', 'provider_vehicle_group_id',
        new_column_name='provider_vehicle_category_id',
    )

    # ------------------------------------------------------------------ #
    # 7. pricing_rules and pricing_outputs — point FK at canonical types  #
    # ------------------------------------------------------------------ #

    # pricing_rules
    op.alter_column(
        'pricing_rules', 'client_vehicle_group_id',
        new_column_name='canonical_type_id',
    )
    op.drop_constraint(
        'fk_pricing_rules_client_group', 'pricing_rules', type_='foreignkey'
    )
    # Cast UUID → INTEGER (all rows deleted in step 1; USING 0 satisfies NOT NULL)
    op.execute("ALTER TABLE pricing_rules ALTER COLUMN canonical_type_id TYPE INTEGER USING 0")
    op.create_foreign_key(
        'fk_pricing_rules_canonical_type',
        'pricing_rules', 'canonical_vehicle_types',
        ['canonical_type_id'], ['id'],
        ondelete='RESTRICT',
    )

    # pricing_outputs
    op.execute("DROP INDEX IF EXISTS ix_pricing_outputs_lookup")
    op.alter_column(
        'pricing_outputs', 'client_vehicle_group_id',
        new_column_name='canonical_type_id',
    )
    op.drop_constraint(
        'fk_pricing_outputs_client_group', 'pricing_outputs', type_='foreignkey'
    )
    # Cast UUID → INTEGER (all rows deleted in step 1; USING 0 satisfies NOT NULL)
    op.execute("ALTER TABLE pricing_outputs ALTER COLUMN canonical_type_id TYPE INTEGER USING 0")
    op.create_foreign_key(
        'fk_pricing_outputs_canonical_type',
        'pricing_outputs', 'canonical_vehicle_types',
        ['canonical_type_id'], ['id'],
        ondelete='RESTRICT',
    )
    op.execute("""
        CREATE INDEX ix_pricing_outputs_lookup
            ON pricing_outputs
            (tenant_id, canonical_type_id, pickup_date,
             duration_days, computed_at DESC)
    """)


def downgrade() -> None:
    # Reverses all schema changes.  Does NOT restore deleted data —
    # this migration is deliberately destructive and the operator
    # must restore from backup if data recovery is needed.

    # ── pricing_outputs ─────────────────────────────────────────────── #
    op.execute("DROP INDEX IF EXISTS ix_pricing_outputs_lookup")
    op.drop_constraint(
        'fk_pricing_outputs_canonical_type', 'pricing_outputs', type_='foreignkey'
    )
    # Cast INTEGER → UUID (no data; gen_random_uuid() satisfies NOT NULL)
    op.execute("ALTER TABLE pricing_outputs ALTER COLUMN canonical_type_id TYPE UUID USING gen_random_uuid()")
    op.alter_column(
        'pricing_outputs', 'canonical_type_id',
        new_column_name='client_vehicle_group_id',
    )
    op.create_foreign_key(
        'fk_pricing_outputs_client_group',
        'pricing_outputs', 'client_vehicle_groups',
        ['client_vehicle_group_id'], ['id'],
        ondelete='RESTRICT',
    )
    op.execute("""
        CREATE INDEX ix_pricing_outputs_lookup
            ON pricing_outputs
            (tenant_id, client_vehicle_group_id, pickup_date,
             duration_days, computed_at DESC)
    """)

    # ── pricing_rules ────────────────────────────────────────────────── #
    op.drop_constraint(
        'fk_pricing_rules_canonical_type', 'pricing_rules', type_='foreignkey'
    )
    # Cast INTEGER → UUID (no data; gen_random_uuid() satisfies NOT NULL)
    op.execute("ALTER TABLE pricing_rules ALTER COLUMN canonical_type_id TYPE UUID USING gen_random_uuid()")
    op.alter_column(
        'pricing_rules', 'canonical_type_id',
        new_column_name='client_vehicle_group_id',
    )
    op.create_foreign_key(
        'fk_pricing_rules_client_group',
        'pricing_rules', 'client_vehicle_groups',
        ['client_vehicle_group_id'], ['id'],
        ondelete='RESTRICT',
    )

    # ── price_observation_heartbeats ─────────────────────────────────── #
    op.alter_column(
        'price_observation_heartbeats', 'provider_vehicle_category_id',
        new_column_name='provider_vehicle_group_id',
    )

    # ── price_observations ───────────────────────────────────────────── #
    op.execute("DROP INDEX ix_price_observations_main")
    op.execute(
        "ALTER TABLE price_observations "
        "RENAME COLUMN provider_vehicle_category_id TO provider_vehicle_group_id"
    )
    op.execute("""
        CREATE INDEX ix_price_observations_main
            ON price_observations
            (provider_id, provider_location_id, provider_rate_id,
             provider_vehicle_group_id, pickup_date, duration_days,
             observed_at DESC)
    """)

    # ── homogeneous_zones ────────────────────────────────────────────── #
    op.execute("DROP INDEX ix_homogeneous_zones_active")
    op.alter_column(
        'homogeneous_zones', 'provider_vehicle_category_id',
        new_column_name='provider_vehicle_group_id',
    )
    op.execute("""
        CREATE INDEX ix_homogeneous_zones_active
            ON homogeneous_zones
            (provider_id, provider_location_id, provider_rate_id,
             provider_vehicle_group_id, start_date, end_date)
            WHERE active = true
    """)

    # ── tenant_vehicle_group_mappings → vehicle_group_mappings ────────── #
    op.drop_constraint(
        'uq_tenant_vehicle_group_mappings_tuple',
        'tenant_vehicle_group_mappings',
        type_='unique',
    )
    op.drop_constraint(
        'fk_tenant_vehicle_group_mappings_canonical_type',
        'tenant_vehicle_group_mappings',
        type_='foreignkey',
    )
    op.alter_column(
        'tenant_vehicle_group_mappings', 'canonical_type_id',
        new_column_name='provider_vehicle_group_id',
    )
    op.alter_column(
        'tenant_vehicle_group_mappings', 'tenant_vehicle_group_id',
        new_column_name='client_vehicle_group_id',
    )
    op.create_foreign_key(
        'fk_vehicle_group_mappings_provider_group',
        'tenant_vehicle_group_mappings', 'provider_vehicle_groups',
        ['provider_vehicle_group_id'], ['id'],
        ondelete='RESTRICT',
    )
    op.create_unique_constraint(
        'uq_vehicle_group_mappings_tuple',
        'tenant_vehicle_group_mappings',
        ['tenant_id', 'client_vehicle_group_id', 'provider_vehicle_group_id'],
    )
    op.rename_table('tenant_vehicle_group_mappings', 'vehicle_group_mappings')

    # ── tenant_vehicle_groups → client_vehicle_groups ────────────────── #
    op.rename_table('tenant_vehicle_groups', 'client_vehicle_groups')

    # ── provider_vehicle_categories → provider_vehicle_groups ─────────── #
    op.execute("DROP INDEX uq_provider_vehicle_categories_canonical")
    op.drop_constraint(
        'fk_provider_vehicle_categories_canonical_type',
        'provider_vehicle_categories',
        type_='foreignkey',
    )
    op.drop_column('provider_vehicle_categories', 'fuel_type')
    op.drop_column('provider_vehicle_categories', 'pending_review')
    op.drop_column('provider_vehicle_categories', 'classification_taxonomy_version')
    op.drop_column('provider_vehicle_categories', 'classification_confidence')
    op.drop_column('provider_vehicle_categories', 'canonical_type_id')
    op.alter_column('provider_vehicle_categories', 'external_code', nullable=False)
    op.rename_table('provider_vehicle_categories', 'provider_vehicle_groups')
    op.create_unique_constraint(
        'uq_provider_vehicle_groups_tuple',
        'provider_vehicle_groups',
        ['provider_id', 'provider_location_id', 'provider_rate_id', 'external_code'],
    )

    # ── DROP canonical_vehicle_types ──────────────────────────────────── #
    op.drop_table('canonical_vehicle_types')
