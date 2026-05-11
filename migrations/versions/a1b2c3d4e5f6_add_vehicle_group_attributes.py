"""add vehicle group attributes to provider_vehicle_groups

Revision ID: a1b2c3d4e5f6
Revises: edccff5a09ca
Create Date: 2026-05-11

Adds four optional columns that the scraper populates when the provider
exposes them.  example_models is NOT NULL (required by contract — see
DATA_MODEL.md Decision 1 / MILESTONES.md REVISION-1); the remaining three
are nullable because not every provider publishes them.

Migration strategy for example_models:
  - Added with server_default='' so existing rows satisfy the NOT NULL constraint.
  - The default is then dropped so future INSERTs must supply the value explicitly.
  - Existing rows will show '' until the scraper runs again and overwrites them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'edccff5a09ca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'provider_vehicle_groups',
        sa.Column('example_models', sa.Text(), nullable=False, server_default=''),
    )
    op.alter_column('provider_vehicle_groups', 'example_models', server_default=None)

    op.add_column(
        'provider_vehicle_groups',
        sa.Column('seats', sa.Integer(), nullable=True),
    )
    op.add_column(
        'provider_vehicle_groups',
        sa.Column('luggage', sa.Integer(), nullable=True),
    )
    op.add_column(
        'provider_vehicle_groups',
        sa.Column('transmission', sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('provider_vehicle_groups', 'transmission')
    op.drop_column('provider_vehicle_groups', 'luggage')
    op.drop_column('provider_vehicle_groups', 'seats')
    op.drop_column('provider_vehicle_groups', 'example_models')
