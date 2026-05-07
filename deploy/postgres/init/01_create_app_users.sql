-- Executed once at container creation via /docker-entrypoint-initdb.d/.
-- Creates the three application roles used by Alembic (admin), the SaaS
-- runtime (app), and privileged back-office operations (super). Idempotent:
-- safe to re-read, but only runs on a fresh volume.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'smart_rental_admin') THEN
    CREATE ROLE smart_rental_admin WITH LOGIN PASSWORD 'smart_rental_admin_dev';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'smart_rental_app') THEN
    CREATE ROLE smart_rental_app WITH LOGIN PASSWORD 'smart_rental_app_dev';
  END IF;
  -- smart_rental_super: BYPASSRLS for operations that must cross tenant
  -- boundaries (e.g. provisioning a new tenant, back-office queries).
  -- It inherits smart_rental_admin's schema privileges via role membership.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'smart_rental_super') THEN
    CREATE ROLE smart_rental_super WITH LOGIN BYPASSRLS PASSWORD 'smart_rental_super_dev';
  END IF;
END
$$;

-- smart_rental_admin: runs Alembic migrations, owns schema objects.
GRANT ALL PRIVILEGES ON DATABASE smart_rental TO smart_rental_admin;
-- Needed to CREATE TABLE / CREATE INDEX inside the public schema.
GRANT CREATE ON SCHEMA public TO smart_rental_admin;

-- smart_rental_app: runtime user for the SaaS application.
-- Full table-level grants are applied per-table in the Alembic migration
-- so that RLS policies remain the single enforcement mechanism.
GRANT CONNECT ON DATABASE smart_rental TO smart_rental_app;
GRANT USAGE ON SCHEMA public TO smart_rental_app;

-- smart_rental_super inherits smart_rental_admin's schema privileges so it
-- can read every table without needing separate GRANTs.
GRANT smart_rental_admin TO smart_rental_super;
GRANT CONNECT ON DATABASE smart_rental TO smart_rental_super;
