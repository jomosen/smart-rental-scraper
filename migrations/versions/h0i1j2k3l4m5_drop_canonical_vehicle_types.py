"""Drop canonical_vehicle_types table (superseded by ACRISS classification).

The table was removed after the ACRISS adoption (Hito C). All references
from provider_vehicle_categories and tenant_vehicle_group_mappings were
already replaced by acriss_code in the previous migration. No active FK
references remain.

Revision ID: h0i1j2k3l4m5
Revises: g9h0i1j2k3l4
"""
from alembic import op

revision = "h0i1j2k3l4m5"
down_revision = "g9h0i1j2k3l4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS canonical_vehicle_types CASCADE")


def downgrade() -> None:
    # Table is intentionally not restored; re-apply from the previous migration
    # if a rollback of the ACRISS adoption is needed.
    pass
