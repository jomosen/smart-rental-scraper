"""create scraping data tables

Revision ID: 745519b5af6f
Revises: 616af4ee21c5
Create Date: 2026-05-07 13:11:28.762198

"""
from datetime import date
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = '745519b5af6f'
down_revision: Union[str, Sequence[str], None] = '616af4ee21c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _next_month(d: date) -> date:
    """Return the first day of the month following d."""
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # scrape_runs                                                          #
    # ------------------------------------------------------------------ #
    op.create_table(
        'scrape_runs',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('provider_id', sa.BigInteger(), nullable=False),
        sa.Column('provider_location_id', sa.BigInteger(), nullable=False),
        sa.Column('provider_rate_id', sa.BigInteger(), nullable=False),
        sa.Column('started_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.Column('finished_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('stats_jsonb', postgresql.JSONB(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')",
            name='ck_scrape_runs_status',
        ),
        sa.ForeignKeyConstraint(
            ['provider_id'], ['providers.id'],
            name='fk_scrape_runs_provider', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['provider_location_id'], ['provider_locations.id'],
            name='fk_scrape_runs_location', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['provider_rate_id'], ['provider_rates.id'],
            name='fk_scrape_runs_rate', ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
    )

    # ------------------------------------------------------------------ #
    # homogeneous_zones                                                    #
    # ------------------------------------------------------------------ #
    op.create_table(
        'homogeneous_zones',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('provider_id', sa.BigInteger(), nullable=False),
        sa.Column('provider_location_id', sa.BigInteger(), nullable=False),
        sa.Column('provider_rate_id', sa.BigInteger(), nullable=False),
        sa.Column('provider_vehicle_group_id', sa.BigInteger(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('representative_date', sa.Date(), nullable=False),
        sa.Column('detected_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.CheckConstraint('end_date >= start_date', name='ck_homogeneous_zones_date_order'),
        sa.CheckConstraint(
            'representative_date BETWEEN start_date AND end_date',
            name='ck_homogeneous_zones_representative_in_zone',
        ),
        sa.ForeignKeyConstraint(
            ['provider_id'], ['providers.id'],
            name='fk_homogeneous_zones_provider', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['provider_location_id'], ['provider_locations.id'],
            name='fk_homogeneous_zones_location', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['provider_rate_id'], ['provider_rates.id'],
            name='fk_homogeneous_zones_rate', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['provider_vehicle_group_id'], ['provider_vehicle_groups.id'],
            name='fk_homogeneous_zones_vehicle_group', ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    # Partial index: only active zones participate in regular query plans.
    # op.create_index does not support WHERE; use raw DDL.
    op.execute("""
        CREATE INDEX ix_homogeneous_zones_active
            ON homogeneous_zones
            (provider_id, provider_location_id, provider_rate_id,
             provider_vehicle_group_id, start_date, end_date)
            WHERE active = true
    """)

    # ------------------------------------------------------------------ #
    # price_observations — PARTITION BY RANGE (observed_at)               #
    # PARTITION BY is not expressible via op.create_table(); use raw DDL. #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE price_observations (
            id                       BIGINT GENERATED BY DEFAULT AS IDENTITY,
            provider_id              BIGINT NOT NULL,
            provider_location_id     BIGINT NOT NULL,
            provider_rate_id         BIGINT NOT NULL,
            provider_vehicle_group_id BIGINT NOT NULL,
            scrape_run_id            BIGINT NOT NULL,
            pickup_date              DATE NOT NULL,
            duration_days            SMALLINT NOT NULL,
            price_per_day            NUMERIC(10,2) NOT NULL,
            total_price              NUMERIC(10,2) NOT NULL,
            currency                 CHAR(3) NOT NULL,
            observed_at              TIMESTAMPTZ NOT NULL,
            CONSTRAINT pk_price_observations
                PRIMARY KEY (id, observed_at),
            CONSTRAINT fk_price_observations_provider
                FOREIGN KEY (provider_id)
                REFERENCES providers(id) ON DELETE RESTRICT,
            CONSTRAINT fk_price_observations_location
                FOREIGN KEY (provider_location_id)
                REFERENCES provider_locations(id) ON DELETE RESTRICT,
            CONSTRAINT fk_price_observations_rate
                FOREIGN KEY (provider_rate_id)
                REFERENCES provider_rates(id) ON DELETE RESTRICT,
            CONSTRAINT fk_price_observations_vehicle_group
                FOREIGN KEY (provider_vehicle_group_id)
                REFERENCES provider_vehicle_groups(id) ON DELETE RESTRICT,
            CONSTRAINT fk_price_observations_scrape_run
                FOREIGN KEY (scrape_run_id)
                REFERENCES scrape_runs(id) ON DELETE RESTRICT
        ) PARTITION BY RANGE (observed_at)
    """)

    # Two initial monthly partitions: current month and the next.
    today = date.today()
    m0 = date(today.year, today.month, 1)
    m1 = _next_month(m0)
    m2 = _next_month(m1)

    op.execute(f"""
        CREATE TABLE price_observations_{m0.year:04d}_{m0.month:02d}
            PARTITION OF price_observations
            FOR VALUES FROM ('{m0.isoformat()}') TO ('{m1.isoformat()}')
    """)
    op.execute(f"""
        CREATE TABLE price_observations_{m1.year:04d}_{m1.month:02d}
            PARTITION OF price_observations
            FOR VALUES FROM ('{m1.isoformat()}') TO ('{m2.isoformat()}')
    """)

    # Main query index (declared on the parent, propagated to partitions).
    op.execute("""
        CREATE INDEX ix_price_observations_main
            ON price_observations
            (provider_id, provider_location_id, provider_rate_id,
             provider_vehicle_group_id, pickup_date, duration_days,
             observed_at DESC)
    """)

    # ------------------------------------------------------------------ #
    # price_observation_heartbeats — one row per tuple, updated in place  #
    # ------------------------------------------------------------------ #
    op.create_table(
        'price_observation_heartbeats',
        sa.Column('provider_id', sa.BigInteger(), nullable=False),
        sa.Column('provider_location_id', sa.BigInteger(), nullable=False),
        sa.Column('provider_rate_id', sa.BigInteger(), nullable=False),
        sa.Column('provider_vehicle_group_id', sa.BigInteger(), nullable=False),
        sa.Column('pickup_date', sa.Date(), nullable=False),
        sa.Column('duration_days', sa.SmallInteger(), nullable=False),
        sa.Column('last_checked_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('last_price_per_day', sa.Numeric(10, 2), nullable=False),
        sa.ForeignKeyConstraint(
            ['provider_id'], ['providers.id'],
            name='fk_heartbeats_provider', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['provider_location_id'], ['provider_locations.id'],
            name='fk_heartbeats_location', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['provider_rate_id'], ['provider_rates.id'],
            name='fk_heartbeats_rate', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['provider_vehicle_group_id'], ['provider_vehicle_groups.id'],
            name='fk_heartbeats_vehicle_group', ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint(
            'provider_id', 'provider_location_id', 'provider_rate_id',
            'provider_vehicle_group_id', 'pickup_date', 'duration_days',
            name='pk_price_observation_heartbeats',
        ),
    )


def downgrade() -> None:
    op.drop_table('price_observation_heartbeats')
    # Dropping the partitioned parent also drops all partitions and
    # the ix_price_observations_main index automatically.
    op.execute('DROP TABLE price_observations')
    # Dropping homogeneous_zones also drops ix_homogeneous_zones_active.
    op.drop_table('homogeneous_zones')
    op.drop_table('scrape_runs')
