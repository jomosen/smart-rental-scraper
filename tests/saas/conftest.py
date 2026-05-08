"""Pytest fixtures shared across all SaaS integration tests.

Requirements:
  - Docker Compose Postgres must be running (docker compose up -d postgres).
  - .env must be populated with SUPER_DATABASE_URL and APP_DATABASE_URL.
  - All Alembic migrations must be applied (alembic upgrade head).

Each test receives a session that is rolled back after the test completes,
leaving the DB in a clean state without truncation or re-migration.
"""
import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()


def _require(var: str) -> str:
    value = os.environ.get(var)
    if not value:
        pytest.skip(f"{var} is not set — skipping integration tests")
    return value


@pytest.fixture(scope="session")
def _super_engine():
    url = _require("SUPER_DATABASE_URL")
    engine = create_engine(url, future=True, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def _app_engine():
    url = _require("APP_DATABASE_URL")
    engine = create_engine(url, future=True, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def super_db_session(_super_engine):
    """Session connected as smart_rental_super (BYPASSRLS).

    Rolled back after each test — no data persists between tests.
    """
    factory = sessionmaker(bind=_super_engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def db_session(_app_engine, super_db_session):
    """Session connected as smart_rental_app (RLS enforced).

    app.tenant_id is NOT pre-set here; individual tests must call
    set_config() or use tenant_context() for tenant-scoped queries.
    """
    factory = sessionmaker(bind=_app_engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
