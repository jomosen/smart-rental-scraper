"""Adopt ACRISS as the vehicle classification standard.

- Create acriss_codes table (materialized subset of the standard)
- Modify provider_vehicle_categories:
    drop canonical_type_id, classification_taxonomy_version, transmission, fuel_type
    add acriss_category, acriss_body_type, acriss_transmission, acriss_fuel (CHAR(1), nullable)
    add acriss_code VARCHAR(4) GENERATED ALWAYS AS (concat of 4 attrs) STORED
    add FK acriss_code → acriss_codes.code
- Modify tenant_vehicle_group_mappings:
    drop canonical_type_id (+ its FK + the unique constraint)
    add acriss_code VARCHAR(4) NOT NULL REFERENCES acriss_codes(code)
    recreate unique constraint with acriss_code

Revision ID: g9h0i1j2k3l4
Revises: f8a9b0c1d2e3
"""
from alembic import op

revision = "g9h0i1j2k3l4"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Create acriss_codes table ─────────────────────────────────────────
    op.execute("""
        CREATE TABLE acriss_codes (
            code            VARCHAR(4)   NOT NULL,
            acriss_category CHAR(1)      NOT NULL,
            acriss_body_type CHAR(1)     NOT NULL,
            acriss_transmission CHAR(1)  NOT NULL,
            acriss_fuel     CHAR(1)      NOT NULL,
            display_name    VARCHAR(128) NOT NULL,
            description     TEXT         NOT NULL DEFAULT '',
            criteria        JSONB        NOT NULL DEFAULT '[]',
            examples        JSONB        NOT NULL DEFAULT '[]',
            active          BOOLEAN      NOT NULL DEFAULT true,
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            last_updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT pk_acriss_codes PRIMARY KEY (code)
        )
    """)
    op.execute("""
        CREATE INDEX ix_acriss_codes_category     ON acriss_codes (acriss_category)
    """)
    op.execute("""
        CREATE INDEX ix_acriss_codes_body_type    ON acriss_codes (acriss_body_type)
    """)
    op.execute("""
        CREATE INDEX ix_acriss_codes_transmission ON acriss_codes (acriss_transmission)
    """)
    op.execute("""
        CREATE INDEX ix_acriss_codes_fuel         ON acriss_codes (acriss_fuel)
    """)
    op.execute("""
        GRANT SELECT, INSERT, UPDATE ON acriss_codes TO smart_rental_app
    """)

    # ── 2. Modify provider_vehicle_categories ─────────────────────────────────
    # Drop FK and column that references canonical_vehicle_types
    op.execute("""
        ALTER TABLE provider_vehicle_categories
            DROP CONSTRAINT IF EXISTS fk_provider_vehicle_categories_canonical_type
    """)
    op.execute("""
        ALTER TABLE provider_vehicle_categories
            DROP COLUMN IF EXISTS canonical_type_id,
            DROP COLUMN IF EXISTS classification_taxonomy_version,
            DROP COLUMN IF EXISTS transmission,
            DROP COLUMN IF EXISTS fuel_type
    """)

    # Add the four individual ACRISS attribute columns
    op.execute("""
        ALTER TABLE provider_vehicle_categories
            ADD COLUMN acriss_category    CHAR(1) NULL,
            ADD COLUMN acriss_body_type   CHAR(1) NULL,
            ADD COLUMN acriss_transmission CHAR(1) NULL,
            ADD COLUMN acriss_fuel        CHAR(1) NULL
    """)

    # Add generated code column — NULL when any attr is NULL
    op.execute("""
        ALTER TABLE provider_vehicle_categories
            ADD COLUMN acriss_code VARCHAR(4) GENERATED ALWAYS AS (
                acriss_category || acriss_body_type || acriss_transmission || acriss_fuel
            ) STORED
    """)

    # FK from generated column to acriss_codes (NO CASCADE — acriss_codes are never deleted)
    op.execute("""
        ALTER TABLE provider_vehicle_categories
            ADD CONSTRAINT fk_pvc_acriss_code
                FOREIGN KEY (acriss_code) REFERENCES acriss_codes (code)
    """)

    op.execute("""
        CREATE INDEX ix_pvc_acriss_category ON provider_vehicle_categories (acriss_category)
    """)
    op.execute("""
        CREATE INDEX ix_pvc_acriss_code     ON provider_vehicle_categories (acriss_code)
    """)

    # ── 3. Modify tenant_vehicle_group_mappings ───────────────────────────────
    # No production data; drop all rows before adding NOT NULL column
    op.execute("DELETE FROM tenant_vehicle_group_mappings")

    op.execute("""
        ALTER TABLE tenant_vehicle_group_mappings
            DROP CONSTRAINT IF EXISTS uq_tenant_vehicle_group_mappings_tuple
    """)
    op.execute("""
        ALTER TABLE tenant_vehicle_group_mappings
            DROP CONSTRAINT IF EXISTS fk_tenant_vehicle_group_mappings_canonical_type
    """)
    op.execute("""
        ALTER TABLE tenant_vehicle_group_mappings
            DROP COLUMN IF EXISTS canonical_type_id
    """)

    op.execute("""
        ALTER TABLE tenant_vehicle_group_mappings
            ADD COLUMN acriss_code VARCHAR(4) NOT NULL
                REFERENCES acriss_codes (code)
    """)
    op.execute("""
        ALTER TABLE tenant_vehicle_group_mappings
            ADD CONSTRAINT uq_tenant_vehicle_group_mappings_tuple
                UNIQUE (tenant_id, tenant_vehicle_group_id, acriss_code)
    """)


def downgrade() -> None:
    # ── 3. Restore tenant_vehicle_group_mappings ──────────────────────────────
    op.execute("DELETE FROM tenant_vehicle_group_mappings")

    op.execute("""
        ALTER TABLE tenant_vehicle_group_mappings
            DROP CONSTRAINT IF EXISTS uq_tenant_vehicle_group_mappings_tuple
    """)
    op.execute("""
        ALTER TABLE tenant_vehicle_group_mappings
            DROP COLUMN IF EXISTS acriss_code
    """)
    op.execute("""
        ALTER TABLE tenant_vehicle_group_mappings
            ADD COLUMN canonical_type_id BIGINT NOT NULL
                REFERENCES canonical_vehicle_types (id)
    """)
    op.execute("""
        ALTER TABLE tenant_vehicle_group_mappings
            ADD CONSTRAINT uq_tenant_vehicle_group_mappings_tuple
                UNIQUE (tenant_id, tenant_vehicle_group_id, canonical_type_id)
    """)

    # ── 2. Restore provider_vehicle_categories ────────────────────────────────
    op.execute("""
        ALTER TABLE provider_vehicle_categories
            DROP CONSTRAINT IF EXISTS fk_pvc_acriss_code
    """)
    op.execute("""
        DROP INDEX IF EXISTS ix_pvc_acriss_code
    """)
    op.execute("""
        DROP INDEX IF EXISTS ix_pvc_acriss_category
    """)
    op.execute("""
        ALTER TABLE provider_vehicle_categories
            DROP COLUMN IF EXISTS acriss_code,
            DROP COLUMN IF EXISTS acriss_category,
            DROP COLUMN IF EXISTS acriss_body_type,
            DROP COLUMN IF EXISTS acriss_transmission,
            DROP COLUMN IF EXISTS acriss_fuel
    """)
    op.execute("""
        ALTER TABLE provider_vehicle_categories
            ADD COLUMN canonical_type_id           INTEGER NULL
                REFERENCES canonical_vehicle_types (id),
            ADD COLUMN classification_taxonomy_version INTEGER NULL,
            ADD COLUMN transmission                VARCHAR(16) NULL,
            ADD COLUMN fuel_type                   VARCHAR(16) NULL
    """)

    # ── 1. Drop acriss_codes table ────────────────────────────────────────────
    op.execute("""
        DROP INDEX IF EXISTS ix_acriss_codes_fuel
    """)
    op.execute("""
        DROP INDEX IF EXISTS ix_acriss_codes_transmission
    """)
    op.execute("""
        DROP INDEX IF EXISTS ix_acriss_codes_body_type
    """)
    op.execute("""
        DROP INDEX IF EXISTS ix_acriss_codes_category
    """)
    op.execute("DROP TABLE IF EXISTS acriss_codes")
