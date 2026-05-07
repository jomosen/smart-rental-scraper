import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Load .env so DATABASE_URL is available when running alembic from the CLI.
# This is a no-op when the variable is already set in the environment
# (e.g. CI, production deployment).
load_dotenv()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Alembic runs as smart_rental_admin (schema owner) so it can ALTER TABLE,
# create indexes, and manage ownership without being blocked by RLS.
# ADMIN_DATABASE_URL must be set; failing loudly here is safer than silently
# falling back to an under-privileged connection.
url = os.environ.get("ADMIN_DATABASE_URL")
if not url:
    raise RuntimeError(
        "ADMIN_DATABASE_URL is not set. "
        "Copy .env.example to .env and configure the admin connection string."
    )
config.set_main_option("sqlalchemy.url", url)

# target_metadata will point to Base.metadata once ORM models are imported.
# Autogenerate is not used for this project (migrations are written by hand),
# so None is safe here.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL without a live DB)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connects to the DB and applies them)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
