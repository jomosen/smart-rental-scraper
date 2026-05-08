# MILESTONES

> Build log of `smart-rental-scraper`. Each milestone has a clear goal, a verifiable closure, and the notable decisions that came out of it. New milestones append to the bottom.
>
> This document is the entry point if you're returning to the project after a break. Read it in order to reconstruct what's been built and why; for the **why behind specific design decisions**, follow the links into `DATA_MODEL.md` and `ROADMAP_ARCHITECTURE.md`.

El **alcance funcional** del producto vive en `docs/PRODUCT_SCOPE.md`.
Este documento (`MILESTONES.md`) registra **cómo y cuándo** se
construye lo que está dentro del alcance, no **qué** está dentro.

---

## How this log works

- **One section per milestone**, in order.
- Each milestone records: what was the goal, what was built, what decisions were taken, and what was deliberately deferred.
- Closure is verifiable: tests passing, end-to-end behaviour confirmed, or both.
- Decisions deferred during the milestone are recorded with a trigger so they don't get forgotten.
- Dates are intentionally absent. The order is what matters; the calendar doesn't.

When a new milestone starts, append a section at the end. Don't rewrite past entries — they are the history. If a past decision is reversed later, the new milestone records the reversal, the old entry stays.

---

## Milestone 0 — Local database infrastructure

**Goal.** Make the local environment ready to host the SaaS database. Postgres running, Alembic configured, environment variables in place. No tables yet.

**What was built.**
- `docker-compose.yml` with a `postgres:16-alpine` service: named volume for persistence, healthcheck, port `5433` exposed (to coexist with another local Postgres on 5432).
- `.env.example` with the database connection variables; `.env` is the user's responsibility.
- `requirements.txt` updated with SQLAlchemy 2.x, Alembic, `psycopg[binary]`, `python-dotenv`.
- Alembic initialised in `migrations/` (not the default `alembic/`), with `env.py` reading `DATABASE_URL` from `.env` via dotenv.
- One empty initial migration to validate the round trip (`upgrade head` → `downgrade base` → `upgrade head` works without errors).

**Decisions taken.**
- **Postgres only, no TimescaleDB.** Keeps the stack minimal; the partition-by-month native to Postgres covers MVP scale. Migration to TimescaleDB stays available without rewrites if volume requires it.
- **Migrations folder at `migrations/`, not `src/saas/`.** Tooling convention; the database is owned by `src/saas/` even though the directory sits at the repo root.

**Deferred.**
- Real schema. Defined in `DATA_MODEL.md`, implemented in Milestone 1 and 2.

**Closure.** Container healthy, migrations idempotent up/down, no tables in the database yet beyond `alembic_version`.

---

## Milestone 1 — Catalog tables

**Goal.** Persist the eight catalog tables defined in `DATA_MODEL.md` Part 2 ("Catalog (global, no `tenant_id`, no RLS)"). All tables that store provider-side data, scrape data, and price observations.

**What was built.**
- Two Alembic migrations:
  - `create_provider_catalog`: `providers`, `provider_locations`, `provider_rates`, `provider_vehicle_groups`.
  - `create_scraping_data_tables`: `scrape_runs`, `homogeneous_zones`, `price_observations` (partitioned monthly), `price_observation_heartbeats`.
- Native Postgres declarative partitioning for `price_observations` (`PARTITION BY RANGE (observed_at)`).
- Two initial partitions: current month and the next, calculated dynamically from the migration date.
- Composite primary key `(id, observed_at)` on `price_observations` (required by partitioning).
- Partial index on `homogeneous_zones` for `WHERE active = true`.
- Foreign keys declared on the partitioned table parent, propagated to all partitions automatically.
- Named constraints (`fk_*`, `pk_*`) for forward compatibility with future migrations that may reference them.

**Decisions taken.**
- `BIGSERIAL` via `sa.Identity(always=False)` instead of legacy `SERIAL`. Modern equivalent without the historical sequence quirks.
- DDL emitted via `op.execute()` for partition-related statements (Alembic's `op.create_table` doesn't support `PARTITION BY`).
- No "preventive" indexes beyond what `DATA_MODEL.md` specifies. Indexes are added when measured query patterns demand them, not preemptively.

**Deferred.**
- **Automatic creation of future partitions of `price_observations`.** Only current and next month exist; inserting rows beyond that range will fail silently in the background. See `DATA_MODEL.md` Part 4 for trigger conditions and migration options.

**Closure.** All migrations apply and reverse cleanly. A test row inserted into `price_observations` with `observed_at` in the current month routes correctly to its partition (`SELECT tableoid::regclass` confirms it).

---

## Milestone 2 — Tenant tables and Row-Level Security

**Goal.** Add the seven tenant-scoped tables from `DATA_MODEL.md` Part 2 ("Tenant-scoped (with `tenant_id`, RLS enabled)") and activate Row-Level Security with proper isolation.

**What was built.**
- One Alembic migration creating: `tenants`, `users`, `client_vehicle_groups`, `vehicle_group_mappings`, `tenant_subscriptions`, `pricing_rules`, `pricing_outputs`.
- Two new Postgres roles created via init script (`deploy/postgres/init/01_create_app_users.sql`):
  - `smart_rental_admin` — owner of all tables.
  - `smart_rental_app` — runtime user, RLS-enforced.
- `FORCE ROW LEVEL SECURITY` enabled on every tenant-scoped table.
- Per-table RLS policy comparing `tenant_id` against `current_setting('app.tenant_id', true)::uuid`, with `WITH CHECK` to prevent cross-tenant writes.
- Special-case policy on `tenants` itself: compares `id` (not `tenant_id`) against the session's `app.tenant_id`, since `tenants` is the root.
- Functional verification with two tenants: queries without context return zero rows, queries scoped to a tenant return only that tenant's data, and INSERT attempts to a different tenant fail with policy violation.

**Decisions taken.**
- **Enforce `WITH CHECK` on RLS policies, not just `USING`.** `USING` covers SELECT/UPDATE/DELETE; without `WITH CHECK`, an INSERT could leak data across tenants. Most painful silent bug averted.
- **`current_setting(..., missing_ok=true)`.** When no `app.tenant_id` is set, the function returns `NULL`, which doesn't match any UUID → zero rows. Fail-safe behaviour: silence over leak.
- **The `init script` is run once at first DB creation only.** Changes to the init script require `docker compose down -v` (the `-v` deletes the volume) before `up -d` to take effect.

**Deferred.**
- **Authentication itself.** External provider to be chosen when the first endpoint with login lands. `users.external_auth_id` is in place to receive the provider's identity reference.
- **Granular roles inside a tenant.** `users.role` exists with `'owner'` as the only value; expand when a customer asks.

**Closure.** RLS verified end-to-end with manual tests:
- Two tenants created, data inserted in each.
- Connecting as `smart_rental_app` without context → 0 rows.
- With context A → only tenant A's rows visible.
- Switching to context B → only tenant B's rows.
- Cross-tenant INSERT → rejected with `new row violates row-level security policy`.

---

## Milestone 3 — Python persistence layer

**Goal.** Build the Python access layer for the database: SQLAlchemy models, session factories with tenant-aware context, and minimal repositories. The scraper continues producing files; this milestone wires up the database access without using it from the scraper yet.

**What was built.**
- A new ownership migration that transfers all existing tables to `smart_rental_admin` (which until now wasn't actually the owner of anything because earlier migrations ran under the superuser). Grants `smart_rental_app` SELECT/INSERT/UPDATE/DELETE on catalog tables and `USAGE/SELECT` on Identity sequences.
- A fourth Postgres role added: `smart_rental_super` (`BYPASSRLS`). Inherits `smart_rental_admin`'s permissions but bypasses RLS for cross-tenant administrative work (creating new tenants, system-wide reports). Solves the bootstrap paradox of "creating a tenant requires `app.tenant_id`, which doesn't exist before the tenant does".
- Three engine factories in `src/saas/infrastructure/persistence/engine.py`: `app_engine`, `admin_engine`, `super_engine`. Each reads its URL from `.env`; missing URL fails loudly.
- Session helpers in `src/saas/infrastructure/persistence/session.py`:
  - `SessionLocal` for normal app use.
  - `tenant_context(session, tenant_id)` — context manager that calls `set_config('app.tenant_id', ..., true)` (the second arg is `is_local`) inside a transaction.
  - `super_session()` — context manager for administrative sessions.
- SQLAlchemy 2.x models for all 15 tables (`models/catalog.py`, `models/tenant.py`), using `Mapped[]` / `mapped_column()` syntax. Native `sa.types.Uuid`, `JSONB` from `postgresql` dialect, `NUMERIC(10,2)` for prices, `DateTime(timezone=True)` for timestamps.
- Minimal repositories under `src/saas/infrastructure/persistence/repositories/`: one per aggregate root the scraper will need in Milestone 4 (provider, provider_location, provider_rate, provider_vehicle_group, scrape_run, homogeneous_zone, price_observation, price_observation_heartbeat).
- The price observation repository implements `insert_if_changed`: only inserts a new row when the price varies from the last recorded row by more than `PRICE_CHANGE_THRESHOLD` (default 0.5%); always upserts the heartbeat. Comparator is the **last recorded `price_observation`**, not the heartbeat (prevents silent drift).
- Alembic migrated to use `ADMIN_DATABASE_URL` instead of `DATABASE_URL`. Failures loudly if not set.
- Integration tests: 8 tests against the real local Postgres, with rollback isolation. Cover repository contracts and one cross-session RLS isolation test.

**Decisions taken.**
- **`set_config('app.tenant_id', ..., true)`** instead of `SET LOCAL app.tenant_id = '...'`. The former accepts bind parameters safely; the latter requires string interpolation.
- **Three engines, three roles, three connection URLs.** Mapping is 1:1 with intent: app for runtime, admin for migrations, super for administrative bypass. Adding a fourth would require a justification stronger than convenience.
- **No abstract `BaseRepository`.** Each repository is independent. Premature abstraction is one of the most expensive errors at this stage.

**Deferred.**
- **Repositories for tenant-scoped tables.** Not needed until the API exists.
- **Test database separate from local development.** Rollback isolation in the local DB is sufficient for MVP.

**Closure.** 32 tests pass (24 existing scraper tests + 8 new persistence tests). All four roles exist, all 15 tables are owned by `smart_rental_admin`, RLS verified functionally and by automated test.

---

## Milestone 4 — Wire the scraper to the database

**Goal.** Make the scraper persist its results to the database via the Milestone-3 repositories. Eliminate the CSV/JSON exporters definitively. End the milestone with the scraper running end-to-end against real providers and writing to Postgres.

**What was built.**
- `CatalogSyncService` (`src/saas/application/catalog_sync.py`): on every run, ensures `providers.json` entries exist as `(provider, provider_location, provider_rate)` rows in the catalog. Idempotent. Uses `super_session` since this is bootstrap operation.
- The orchestrator (`SmartScraperOrchestrator`) was modified to:
  - Sync the catalog at the start of every run.
  - For each provider entry: create a `scrape_run`, run probe → analyse → persist zones (replace) → extract → persist observations (insert-if-changed) → mark the run finished. Failures on one provider don't abort the whole orchestrator run.
- Removed code:
  - `ResultExpander` and its tests.
  - `ResultExporter`, `SeasonExporter`, `GapExporter`.
  - `JsonSeasonBoundaryRepository` (zones now live in the DB).
- Two new test suites:
  - `test_catalog_sync.py` — three tests covering creation, idempotency, and update-on-change.
  - `test_orchestrator_persistence.py` — four async tests with mocked scrapers (no Playwright in tests) verifying the orchestrator creates runs, marks failures, persists zones via replace, and persists observations via insert-if-changed.
- A test helper `_cleanup_provider` that deletes data in FK-safe order in `finally` blocks. Required because the orchestrator opens its own sessions internally; tests can't rely on rollback isolation alone.
- Logging at INFO level: each run logs provider, location, rate, run_id, duration, zones detected, observations inserted vs skipped.

**Decisions taken.**
- **No CSV/JSON output. The database is the only sink.** This decision is more aggressive than the safer alternative (parallel exports during transition). The choice was made knowing that the customer has nothing to consume from the system until an API exists. The customer was informed before this milestone.
- **`pytest-asyncio` with `asyncio_mode = auto`.** Async tests don't need decorators; cleaner.
- **Test fixtures live at `tests/saas/conftest.py`** so they're auto-discovered by all `tests/saas/...` modules without `pytest_plugins` declarations in each test file.
- **Synthetic data is not stored, period.** The `is_synthetic` flag stays in the in-memory `BookingResult` for whatever in-process consumers might want it, but the database never sees it. Synthetic-day prices will be derived on read by a future `PriceQueryService`.

**Deferred.**
- **`PriceQueryService`** that derives prices for non-representative days by joining zones and observations. Lands when the API is built.
- **Pre-`main.py` audit of the model rename.** The shared/scraper refactor (`BookingResult.provider` → `BookingResult.provider_name`) had stale references in `main.py` that the test suite missed. Caught manually before the milestone. Lesson noted: after model refactors, run the entry point end-to-end before declaring victory.

**Closure.** 32 tests pass (the count drops from 17 to 17 in scraper tests after removing the 7 `ResultExpander` tests, and gains 7 new tests in orchestrator and catalog_sync — net 32 stays the same by coincidence, not by design). Real scraper run against one active provider produces coherent counts in the DB:

```
providers_count       | 1
locations_count       | 1
rates_count           | 1
groups_count          | 14
runs_count            | 1
active_zones          | 3
observations_count    | 378
heartbeats_count      | 378
```

378 = 14 vehicle groups × 3 zones × 9 durations. Matches the volume reasoning in `DATA_MODEL.md` Part 3.

---

## Audit checkpoint after Milestone 4

Before opening the next block, all five operational documents (`CLAUDE.md`, `README.md`, `DATA_MODEL.md`, `ROADMAP_ARCHITECTURE.md`, `SCRAPING_OPTIMIZATIONS.md`) were reviewed and brought up to date with the implemented reality of Milestones 0–4. Inconsistencies found and fixed:

- `CLAUDE.md`: pipeline section referenced eliminated `ResultExpander`; testing section listed deleted tests; configuration section under-mentioned the database URLs.
- `README.md`: rewritten substantially. The PoC framing was outdated; project structure didn't reflect the monorepo; outputs section still referenced CSV/JSON.
- `DATA_MODEL.md`: RLS language softened from "recommended" to "implemented"; the four Postgres roles were missing entirely; partitioning note added warning about future-month creation; minor terminology fixes.
- `ROADMAP_ARCHITECTURE.md`: the "6-8 week plan" section was replaced by four conceptual phases linking back to this `MILESTONES.md`.
- `SCRAPING_OPTIMIZATIONS.md`: pipeline reference to `ResultExpander` updated.

The five documents are now consistent with each other and with the code as of the closure of Milestone 4.

---

## Next block — candidates

The order of the next milestones depends on a conversation with the customer (how they want to consume the data, what pricing rules they have in mind, whether a second customer is incoming). Recommended order, conditional on that input:

**Candidate 5D — Tenant onboarding (preferred first).** A CLI script or admin tool to create a tenant, its `client_vehicle_groups`, the `vehicle_group_mappings` to provider groups, and `tenant_subscriptions`. Without UI yet. Lands a real first tenant with real mappings into the system. Most informative milestone for catching gaps in the model.

**Candidate 5A — Read-only HTTP API.** FastAPI, auth still pending (probably a static admin token initially), endpoints to list observed prices, zones, gaps. No write operations yet. Gives the customer a way to query the system without poking the DB directly.

**Candidate 5C — Automatic partition creation.** Scheduled job or startup hook that ensures next-month partitions of `price_observations` exist before any scrape. Operationally important; invisible to the customer until the moment it would have failed silently.

**Candidate 5B — Minimum viable pricing engine.** Hard-coded rules in Python (no DSL yet) producing `pricing_outputs` for a real or fictitious tenant. Validates the chain `observations → rules → outputs` before investing in abstraction. Worth doing only after at least one real customer has expressed concrete rules they'd want.

Recommended order: **5D → 5A → 5C → 5B**. The reasoning is that a real tenant with mappings teaches more about the model's gaps than any amount of additional abstraction; then exposing a read API lets the customer see something; then the partition automation prevents the silent August failure; and only then does it make sense to build pricing logic on top of all of that.

---

## Milestone 5D-A — Tenant onboarding CLI

**Goal.** Provide an operator-facing command that creates a fully configured tenant in the database: catalog validation, tenant creation, vehicle-group discovery, owner user, client vehicle groups, provider-to-client mappings, and subscription activation. Eight steps, automatic rollback on failure, readable error messages at every exit.

**What was built.**
- `src/saas/application/onboarding/config.py`: `OnboardingConfig` dataclasses and `load_config(path)`. Parses and validates a tenant YAML config file. **Option B schema**: mappings live inside `subscriptions` (not `vehicle_groups`), keyed by `client_group_code` referencing a `vehicle_groups` entry. Validation: non-empty required fields, 3-letter ASCII-alpha currency, email format, `display_order` required integer ≥ 0, unique `(provider_code, location_code, rate_code)` tuples (allows multiple subscriptions to the same provider), `client_group_code` must reference a declared vehicle group, `external_codes` must be non-empty.
- `src/saas/application/onboarding/steps.py`: Seven step functions and `OnboardingError`. Key properties: `step_validate_catalog` returns `dict[tuple[str,str,str], tuple[int,int,int]]` keyed by `(provider_code, location_code, rate_code)`, enabling multiple subscriptions per provider. `step_create_tenant` fails loudly if a tenant with the same name already exists. `step_discovery` delegates all cross-boundary work to `discovery.py` and takes `scraper_factory` as a required parameter. `step_create_users_and_groups` warns on stderr that `external_auth_id=NULL`. `step_create_mappings` iterates over subscriptions (not vehicle groups) to resolve mappings by tuple. `step_create_subscriptions` returns `dict[tuple, uuid.UUID]`. `step_activate_subscriptions` validates mapping completeness — subscriptions with any unmapped active `provider_vehicle_group` stay in `pending_mapping` with a stderr warning.
- `src/saas/application/onboarding/rollback.py`: `rollback_tenant(tenant_id, session)` — deletes all tenant rows in FK-safe order. Unchanged from first pass.
- `src/saas/application/discovery.py` (new): dedicated cross-boundary module concentrating all `src/scraper/` imports in one place. Contains `build_scraper_factory(providers_json)` and `run_discovery_for_tuple(...)`. Acknowledged technical debt until Phase 3 (scheduling and workers) replaces inline scraping with a job-queue call.
- `src/saas/application/onboarding/cli.py`: `main()` entry point. Steps 4–7 run inside a **single** `tenant_context` transaction (no partial state committed between steps). `IntegrityError` on step 2 produces a readable message. Step 8 reports active vs. pending_mapping counts. Rollback messages include `tenant_id` for manual cleanup if rollback itself fails.
- `docs/onboarding-example.yaml`: annotated template updated to Option B schema.
- `pyyaml>=6.0` added to `requirements.txt`.
- 12 tests in `tests/saas/application/test_onboarding.py` (5 unit in `TestLoadConfig`, 7 integration as top-level functions).

**Decisions taken.**
- **Option B: mappings inside subscriptions.** The first pass put mappings inside `vehicle_groups`, which imposed a false constraint that each `vehicle_group` could map to only one `(provider, location, rate)` tuple. Option B scopes each mapping to its subscription tuple, correctly reflecting that the same client group can have different external codes across locations.
- **Multiple subscriptions to the same provider allowed.** The uniqueness constraint is now on `(provider_code, location_code, rate_code)`, not just `provider_code`. `DATA_MODEL.md` was already correct; the first-pass config validation was wrong.
- **Steps 4–7 share one transaction.** Four separate `tenant_context` opens in the first pass could silently commit partial state (e.g. users created but mappings failed). One transaction means the DB only ever sees the complete set or nothing.
- **`step_activate_subscriptions` validates mapping completeness.** A subscription is only promoted to `active` if every active `provider_vehicle_group` for its tuple has a `VehicleGroupMapping` in the tenant. Incomplete subscriptions stay in `pending_mapping` with an operator warning rather than being silently skipped.
- **`scraper_factory` is a required parameter in `step_discovery`.** Passing `None` was a footgun that silently worked in tests (because `_run_session` was mocked) but would crash in production. The factory is now built explicitly in the CLI via `discovery.build_scraper_factory()`.
- **Cross-boundary imports isolated to `discovery.py`.** All `src/saas/` → `src/scraper/` imports are concentrated in one module with a prominent comment. This is the designated coupling point until Phase 3 physically separates the scraper process.
- **`step_create_tenant` fails loud on duplicate name.** A name-existence check raises `OnboardingError` before the INSERT, giving the operator a clear message instead of a raw DB exception.
- **`SEASON_PRICE_THRESHOLD` read from environment in `discovery.py`.** Pattern mirrors `container.py:77` — `float(os.environ.get("SEASON_PRICE_THRESHOLD", "0.05"))`.

**Deferred.**
- **`--dry-run` flag.** Would validate all steps without committing. Deferred until operators use the CLI regularly enough to need it.
- **`--tenant-id` resume.** If rollback fails, the operator must clean up manually. A flag to resume from a known partial state is deferred until it becomes necessary.
- **Authentication.** `users.external_auth_id` is left `NULL` and the operator is warned. Will be set when the identity provider is chosen.
- **Automatic partition creation for `price_observations`.** Discovery can insert observations into future months; partitions must exist or inserts fail silently. Deferred to Milestone 5C.

**Closure.** `pytest tests/saas/application/test_onboarding.py` passes (12/12, 5 unit + 7 integration). All pre-existing tests (32) continue to pass.
