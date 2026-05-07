"""create provider catalog

Revision ID: 616af4ee21c5
Revises: 70c9f8abbcb0
Create Date: 2026-05-07 13:11:28.151894

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '616af4ee21c5'
down_revision: Union[str, Sequence[str], None] = '70c9f8abbcb0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'providers',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('code', sa.String(32), nullable=False),
        sa.Column('display_name', sa.Text(), nullable=False),
        sa.Column('scraper_key', sa.String(64), nullable=False),
        sa.Column('default_currency', sa.CHAR(3), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='active'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.CheckConstraint(
            "status IN ('active', 'beta', 'deprecated', 'broken')",
            name='ck_providers_status',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_providers_code'),
    )

    op.create_table(
        'provider_locations',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('provider_id', sa.BigInteger(), nullable=False),
        sa.Column('location_code', sa.String(16), nullable=False),
        sa.Column('location_name', sa.Text(), nullable=False),
        sa.Column('country', sa.String(2), nullable=True),
        sa.Column('city', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.ForeignKeyConstraint(
            ['provider_id'], ['providers.id'],
            name='fk_provider_locations_provider', ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'provider_id', 'location_code',
            name='uq_provider_locations_provider_location',
        ),
    )

    op.create_table(
        'provider_rates',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('provider_id', sa.BigInteger(), nullable=False),
        sa.Column('rate_code', sa.String(32), nullable=False),
        sa.Column('rate_name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.ForeignKeyConstraint(
            ['provider_id'], ['providers.id'],
            name='fk_provider_rates_provider', ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'provider_id', 'rate_code',
            name='uq_provider_rates_provider_rate',
        ),
    )

    op.create_table(
        'provider_vehicle_groups',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('provider_id', sa.BigInteger(), nullable=False),
        sa.Column('provider_location_id', sa.BigInteger(), nullable=False),
        sa.Column('provider_rate_id', sa.BigInteger(), nullable=False),
        sa.Column('external_code', sa.String(64), nullable=False),
        sa.Column('external_name', sa.Text(), nullable=False),
        sa.Column('first_seen_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.Column('last_seen_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.ForeignKeyConstraint(
            ['provider_id'], ['providers.id'],
            name='fk_provider_vehicle_groups_provider', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['provider_location_id'], ['provider_locations.id'],
            name='fk_provider_vehicle_groups_location', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['provider_rate_id'], ['provider_rates.id'],
            name='fk_provider_vehicle_groups_rate', ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'provider_id', 'provider_location_id', 'provider_rate_id', 'external_code',
            name='uq_provider_vehicle_groups_tuple',
        ),
    )


def downgrade() -> None:
    op.drop_table('provider_vehicle_groups')
    op.drop_table('provider_rates')
    op.drop_table('provider_locations')
    op.drop_table('providers')
