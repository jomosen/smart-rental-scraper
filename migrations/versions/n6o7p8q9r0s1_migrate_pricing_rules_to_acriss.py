"""Migrate pricing_rules and pricing_outputs: canonical_type_id -> acriss_code.

Revision ID: n6o7p8q9r0s1
Revises: m5n6o7p8q9r0
Create Date: 2026-06-02

Context
-------
pricing_rules.canonical_type_id and pricing_outputs.canonical_type_id were
INTEGER columns referencing the now-dropped canonical_vehicle_types table
(that table was dropped with CASCADE in h0i1j2k3l4m5, which also dropped the
foreign-key constraints, leaving the columns as bare INTEGERs with no FK).

Target schema (DATA_MODEL.md §2):
    acriss_code  VARCHAR(4) NULL  FK → acriss_codes.code

NULL is intentional: a pricing_rule that stores a cross-provider configuration
applies to ALL categories (overrides per category live inside formula_jsonb).
See MILESTONES.md D5 decision "El versionado es de la configuración completa".

Both tables are empty in any real installation (no production tenants exist yet;
the column rename in b1c2d3e4f5a6 zeroed the data). USING NULL is safe.

Must run as smart_rental_admin (Alembic's ADMIN_DATABASE_URL).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'n6o7p8q9r0s1'
down_revision: Union[str, Sequence[str], None] = 'm5n6o7p8q9r0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the lookup index before altering the column it references.
    op.execute("DROP INDEX IF EXISTS ix_pricing_outputs_lookup")

    # ── pricing_rules ──────────────────────────────────────────────────────── #
    op.execute(
        "ALTER TABLE pricing_rules RENAME COLUMN canonical_type_id TO acriss_code"
    )
    op.execute(
        "ALTER TABLE pricing_rules ALTER COLUMN acriss_code DROP NOT NULL"
    )
    op.execute(
        "ALTER TABLE pricing_rules "
        "ALTER COLUMN acriss_code TYPE VARCHAR(4) USING NULL"
    )
    op.create_foreign_key(
        'fk_pricing_rules_acriss_code',
        'pricing_rules', 'acriss_codes',
        ['acriss_code'], ['code'],
        ondelete='RESTRICT',
    )

    # ── pricing_outputs ────────────────────────────────────────────────────── #
    op.execute(
        "ALTER TABLE pricing_outputs RENAME COLUMN canonical_type_id TO acriss_code"
    )
    op.execute(
        "ALTER TABLE pricing_outputs ALTER COLUMN acriss_code DROP NOT NULL"
    )
    op.execute(
        "ALTER TABLE pricing_outputs "
        "ALTER COLUMN acriss_code TYPE VARCHAR(4) USING NULL"
    )
    op.create_foreign_key(
        'fk_pricing_outputs_acriss_code',
        'pricing_outputs', 'acriss_codes',
        ['acriss_code'], ['code'],
        ondelete='RESTRICT',
    )

    # Recreate lookup index with the new column name.
    op.execute("""
        CREATE INDEX ix_pricing_outputs_lookup
            ON pricing_outputs
            (tenant_id, acriss_code, pickup_date, duration_days, computed_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_pricing_outputs_lookup")

    # ── pricing_outputs ────────────────────────────────────────────────────── #
    op.drop_constraint('fk_pricing_outputs_acriss_code', 'pricing_outputs',
                       type_='foreignkey')
    op.execute(
        "ALTER TABLE pricing_outputs ALTER COLUMN acriss_code TYPE INTEGER USING 0"
    )
    op.execute(
        "ALTER TABLE pricing_outputs ALTER COLUMN acriss_code SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE pricing_outputs RENAME COLUMN acriss_code TO canonical_type_id"
    )
    op.execute("""
        CREATE INDEX ix_pricing_outputs_lookup
            ON pricing_outputs
            (tenant_id, canonical_type_id, pickup_date, duration_days, computed_at DESC)
    """)

    # ── pricing_rules ──────────────────────────────────────────────────────── #
    op.drop_constraint('fk_pricing_rules_acriss_code', 'pricing_rules',
                       type_='foreignkey')
    op.execute(
        "ALTER TABLE pricing_rules ALTER COLUMN acriss_code TYPE INTEGER USING 0"
    )
    op.execute(
        "ALTER TABLE pricing_rules ALTER COLUMN acriss_code SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE pricing_rules RENAME COLUMN acriss_code TO canonical_type_id"
    )
