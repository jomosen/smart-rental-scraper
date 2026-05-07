"""SQLAlchemy engine factory for the SaaS persistence layer.

Three engines, each bound to a different database role:

  app_engine   — smart_rental_app (RLS enforced, required for all runtime ops)
  admin_engine — smart_rental_admin (schema owner, optional; used by scripts)
  super_engine — smart_rental_super (BYPASSRLS, optional; used for tenant
                 provisioning and back-office queries)

Only app_engine is required. The others raise RuntimeError if the
corresponding environment variable is not set, so callers must opt in
explicitly rather than discovering they have elevated access by accident.
"""
import os

from sqlalchemy import create_engine, Engine

_ECHO = os.environ.get("SQL_ECHO", "").lower() in ("1", "true", "yes")


def _make_engine(url: str) -> Engine:
    return create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        echo=_ECHO,
    )


def app_engine() -> Engine:
    url = os.environ.get("APP_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "APP_DATABASE_URL is not set. "
            "Copy .env.example to .env and configure the app connection string."
        )
    return _make_engine(url)


def admin_engine() -> Engine:
    url = os.environ.get("ADMIN_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "ADMIN_DATABASE_URL is not set. "
            "Copy .env.example to .env and configure the admin connection string."
        )
    return _make_engine(url)


def super_engine() -> Engine:
    url = os.environ.get("SUPER_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "SUPER_DATABASE_URL is not set. "
            "Copy .env.example to .env and configure the super connection string."
        )
    return _make_engine(url)
