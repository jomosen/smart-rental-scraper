"""Session factory and context managers for the SaaS persistence layer.

Usage:

    # Normal tenant-scoped request:
    with tenant_context(session_factory, tenant_id) as session:
        repos = ...

    # Back-office / tenant provisioning (BYPASSRLS):
    with super_session(super_engine_instance) as session:
        ...
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker, Session as SASession


def make_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


# Module-level factory — initialised by the application bootstrap.
# Tests override this via fixtures.
SessionLocal: sessionmaker | None = None


@contextmanager
def tenant_context(
    factory: sessionmaker,
    tenant_id: uuid.UUID | str,
) -> Generator[SASession, None, None]:
    """Yield a session with app.tenant_id set for the duration of the transaction.

    Uses SET LOCAL via set_config() so the setting is transaction-scoped and
    automatically reverts on commit/rollback. Bound parameters prevent injection.
    """
    session: SASession = factory()
    try:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def super_session(engine) -> Generator[SASession, None, None]:
    """Yield a session connected as smart_rental_super (BYPASSRLS).

    Use only for tenant provisioning and back-office operations that must
    cross tenant boundaries. Never expose this session to untrusted callers.
    """
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session: SASession = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
