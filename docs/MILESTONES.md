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
- 12 tests in `tests/saas/application/test_onboarding.py` (5 unit in `TestLoadConfig`, 7 integration as top-level functions). A subsequent polish pass (same milestone) added a 13th test — `test_leaves_subscription_pending_when_some_provider_groups_unmapped` — covering the partial-mapping edge case: two PVGs (ECMR + SUV1), only ECMR mapped, subscription must stay `pending_mapping` and the stderr warning must name "SUV1". The same pass improved `step_activate_subscriptions` to include the actual external codes of unmapped groups in the warning (not just the count), removed a defensive `if ts is not None` guard that was semantically wrong (the subscription row is always present at this point), and updated the CLI to print steps 4–7 as a single `[4-7/8]` line reflecting their atomicity, with a more helpful `IntegrityError` message that distinguishes a duplicate-tenant constraint from other DB errors.

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

**Closure.** `pytest tests/saas/application/test_onboarding.py` passes (13/13 after polish, 5 unit + 8 integration). All pre-existing tests (32) continue to pass.

---

## Milestone 5D-B — PriceQueryService

**Goal.** Implement the three market-price queries defined in `PRODUCT_SCOPE.md` v0 as a Python service. No HTTP, no CLI — pure application logic with DTOs and tests.

**What was built.**
- `src/saas/application/price_query/dtos.py`: `ZoneRange`, `FormatARow`, `FormatATable`. `FormatARow` holds `prices_by_duration: dict[int, Optional[Decimal]]` (None when no observation) and `coverage: Optional[int]` (None for per-provider tariff, int for aggregates). Decimal throughout — no float.
- `src/saas/application/price_query/rejilla.py`: `compute_intersected_grid(zones_by_provider, date_range)` — pure function, no DB dependencies. Collects all zone-boundary cut points from all providers, clips them to `date_range`, and returns consecutive (start, end) inclusive tramos. Returns `[]` when no zones exist.
- `src/saas/application/price_query/service.py`: `PriceQueryService` with three public methods. Session injected by caller; no internal session management. All tenant-scoped queries include an application-layer `tenant_id` filter in addition to RLS.
  - `get_provider_tariff`: resolves `(provider_code, location_code, rate_code)` → IDs, checks active subscription, loads zones and observations, builds one `FormatARow` per (client_group × zone) clipped to `date_range`. N:M min policy per client group.
  - `get_market_average_tariff`: loads all active subscriptions, builds intersected grid from all their zones, aggregates prices with arithmetic mean (quantized to 0.01).
  - `get_market_minimum_tariff`: same grid, aggregates with `min`.
  - Shared private `_get_market_tariff("average"|"minimum")` implements both aggregate methods.
  - `_fetch_observations` uses `DISTINCT ON (pvg_id, pickup_date, duration_days) ... ORDER BY ... observed_at DESC` — the canonical index pattern from `DATA_MODEL.md` Part 3.
  - `coverage` = count of subscriptions contributing at least one non-None price to the row (not structural zone coverage, not per-duration).
- `src/saas/application/price_query/__init__.py`: re-exports `PriceQueryService`, `FormatARow`, `FormatATable`, `ZoneRange`.
- 15 tests in `tests/saas/application/test_price_query_service.py` (5 unit in `TestComputeIntersectedGrid`, 10 integration as top-level functions).

**Decisions taken.**
- **Decimal, not float.** `price_per_day` is `NUMERIC(10,2)`. Averages quantized to `Decimal("0.01")`. Minimum is exact (no rounding needed).
- **DISTINCT ON over MAX(observed_at) subquery.** Follows the canonical query in `DATA_MODEL.md` Part 3. The existing index `(provider_id, location_id, rate_id, pvg_id, pickup_date, duration_days, observed_at DESC)` makes this efficient.
- **coverage defined as "subscriptions contributing at least one price".** A subscription counts toward coverage if it has a zone covering the tramo AND at least one price observation for any duration. A subscription with a zone but no observations contributes 0 to coverage.
- **Intersected grid from zone ranges, not zone ORM objects.** `compute_intersected_grid` receives `dict[int, list[ZoneRange]]` where the int key is a per-call index. The function is pure and testable without DB.
- **Single aggregate implementation.** `get_market_average_tariff` and `get_market_minimum_tariff` delegate to `_get_market_tariff` with `aggregate="average"|"minimum"`. Avoids code duplication at the cost of one internal branch.
- **No session opened internally.** The service is a pure orchestrator over the injected session. Caller controls transaction scope — required for compatibility with both `super_session` (BYPASSRLS) and `tenant_context` (app role + RLS).

**Deferred.**
- **API HTTP** exposing the three methods (Milestone 5A).
- **CLI demo** for interactive querying (Milestone 5D-C).
- **Format B** (day × duration matrix) — derivable from Format A on demand; not implemented.
- **Intermediate-duration interpolation** — durations outside `{1,2,3,4,5,6,7,14,21,28}` return None; provider pricing logic for intermediates is deferred per `DATA_MODEL.md` Part 4.

**Closure.** `pytest tests/` passes (60/60, 45 pre-existing + 15 new). 5 unit tests on the pure `compute_intersected_grid` function, 10 integration tests covering all three service methods including N:M policy, partial coverage, inactive-subscription exclusion, and RLS isolation.

Pulidos posteriores incluidos en este hito:
- `coverage` es ahora un dict por duración (`coverage_by_duration: Optional[dict[int, int]]`),
  fiel al contrato escrito en `PRODUCT_SCOPE.md` ("cada celda lleva un campo coverage").
- `_find_representative` documenta su precondición de forma fuerte (marcador PRECONDITION).
- Validación temprana de coherencia de contexto de sesión: `ValueError` si `app.tenant_id`
  de sesión != `tenant_id` pedido (`_assert_session_tenant_consistent`).
- 2 tests nuevos: `test_coverage_differs_across_durations_within_same_row` y
  `test_raises_when_session_tenant_mismatches_requested_tenant`. Total: 62/62.

---

## Milestone 5D-C — Demo CLI

**Goal.** Provide a command-line interface that executes one of the three `PriceQueryService` queries and renders the result as a human-readable Format A table in the terminal. Last piece of Milestone 5D.

**What was built.**
- `src/saas/application/demo/__init__.py` and `__main__.py`: package entry points; `python -m src.saas.application.demo` invokes `main()`.
- `src/saas/application/demo/cli.py`: argparse-based CLI with full argument validation (UUID parsing, date-range ordering, provider-arg presence for `--query=provider`, warning when provider args are ignored for aggregate queries). Opens `tenant_context` session (app role, RLS enforced). Resolves tenant name, active subscription count, and client group display names from the same session before rendering.
- `src/saas/application/demo/formatter.py`: pure function `format_table(table, query_type, tenant_name, extra_context) → str`. Uses `rich` for Unicode box drawing: `Panel` for the header box, `Table(box=SQUARE)` for each client group. Coverage column present only in average/minimum mode. Em dash `—` for None prices. Soft-wrapping for warning messages to avoid mid-sentence line breaks.
- `tests/saas/application/test_demo.py`: 7 unit tests on `format_table` (no DB), 5 integration-style tests on `main()` with `PriceQueryService` mocked.
- `requirements.txt`: added `rich>=13.0`.

**Decisions taken.**
- **`rich` for output rendering.** `Panel` gives the `╭─╮` header box from the spec with zero boilerplate. `Table(box=SQUARE)` gives `┌─┬─┐` data tables. Output captured via `Console(file=io.StringIO())`, making `format_table` a pure function testable without a terminal.
- **Period in tramos uses DD/MM when the entire date_range is within one year.** Same-year detection is done once from `date_range` metadata and applied to all tramo cells. The header always shows full DD/MM/YYYY.
- **Coverage compacted to "N/T" or "N1-N2/T".** When all duration cells in a row have the same coverage, a single value is shown. When they differ, the range is shown. T = `num_subscriptions` from `extra_context`, resolved by the CLI via a `SELECT count(*)` on `tenant_subscriptions`.
- **`format_table` is a pure function.** No session, no DB, no tenant_id. All context (tenant name, group names, subscription count, provider names for provider mode) flows through `extra_context`. The CLI resolves these and passes them in.
- **Validation fails fast with exit code 1 and a clear message to stderr.** No traceback is shown. Argparse-level failures (unknown args) are also caught. `_CliError` is the private exception type for validation failures.
- **`tenant_context` (app role, RLS enforced).** The CLI represents a legitimate product consumer, not an administrative operation. `super_session` is never used here.

**Deferred.**
- **Output to CSV / Excel / JSON** exportable format — will be necessary in Phase 4 when delivering operational tariffs to client systems (see `PRODUCT_SCOPE.md` "Integración con sistema externo del cliente").
- **Colored output by price thresholds** (highlight cheapest/most expensive cells). Useful visual feature but not v0.

**Closure.** `pytest tests/` passes (74/74, 62 pre-existing + 12 new). 7 unit tests on the pure `format_table` function, 5 CLI tests with mocked service. Hito 5D cerrado.

---

## Known Issues — RESOLVED

### KI-1 — Zone replication to all provider_vehicle_groups

**Problem.** `SmartScraperOrchestrator._persist_zones` was only writing zone rows for the single car group chosen by `PricePointExtractor` (the first car in the probe result). For a provider with 14 vehicle groups, 13 groups had no zones and thus no price observations were ever written for them.

**Root cause.** Phase 2 originally called `analyzer.detect_zones(price_points, ..., car_group)` once per group that appeared in `price_points`. Since `PricePointExtractor.extract` returns only the first car per search result, only one group was represented in `price_points`. The old `_persist_zones` wrote zones only for that group.

**Fix (committed with this milestone).**
1. Added `detect_zones_provider_level` to `SeasonAnalyzer`: aggregates all price points by pickup date (mean daily price across all groups per date) and detects boundaries on the aggregated temporal signal, avoiding spurious boundaries from cross-group price differences. Resulting zones carry `car_group=""`.
2. Added `list_active_for_tuple` to `ProviderVehicleGroupRepository`: returns all active PVGs for a (provider, location, rate) tuple.
3. Rewrote Phase 2 and `_persist_zones` in `SmartScraperOrchestrator`: now detects one set of provider-level zones, then replicates them to ALL active `provider_vehicle_groups`. Groups seen in probe results are upserted first (via `upsert_seen`) so newly-discovered groups receive zones immediately.
4. `probe_groups` is now extracted from `probe_results` (all car groups across all results), not from `price_points` (which only contains the first car per search).
5. Fixed missing `7` in `_DEFAULT_EXTRACTION_DURATIONS` (was `[1,2,3,4,5,6,14,21,28]`, corrected to `[1,2,3,4,5,6,7,14,21,28]`).
6. Fixed `NameError` in Phase 3 where `all_zones` was referenced after renaming to `provider_zones`.

**Tests added.**
- `TestDetectZonesProviderLevel` (3 tests) in `tests/test_season_analyzer.py`.
- `TestOrchestratorZoneReplication` (3 tests) in `tests/saas/application/test_orchestrator_persistence.py`.

**Closure.** `pytest tests/` passes (80/80, 74 pre-existing + 6 new).

---

### REVISION-1 — Subscription activation: completeness → partial

Decisión original (DATA_MODEL.md Decisión 1, ya editada):
"Una subscription solo puede llegar a `active` cuando todos los
`provider_vehicle_groups` están mapeados o explícitamente ignorados."

Realidad: el mecanismo de "explícitamente ignorados" nunca se construyó. La validación
estricta bloqueaba casos legítimos (cliente con flota menor que el catálogo del provider).
En el primer onboarding real con datos reales (provider Solcar, 14 grupos en catálogo,
tenant Mardrive con 1 grupo mapeado), la subscription quedaba en `pending_mapping`
indefinidamente sin forma de avanzar.

Decisión revisada: una subscription se activa con cualquier número ≥1 de mappings. Los
grupos no mapeados quedan fuera del scope del tenant y no aparecen en las queries de
`PriceQueryService` (ya funciona así por construcción: lee `vehicle_group_mappings`, no
`provider_vehicle_groups` directamente). Si en el futuro aparece el mecanismo de
"explícitamente ignorado" o auditoría más estricta, se revisa entonces.

Cambios:
- `step_activate_subscriptions` en `steps.py`: activa con ≥1 mapping, [warning] solo
  con 0 mappings, [info] (no bloqueante) con grupos fuera de scope.
- DATA_MODEL.md Decisión 1: texto actualizado con nueva semántica y referencia a contexto.
- Tests adaptados: `test_leaves_subscription_pending_when_no_mappings_exist` (ex
  `...when_provider_groups_unmapped`), `test_activates_subscription_with_partial_mappings`
  (ex `...when_some_provider_groups_unmapped`, aserción cambiada a `active`).
- Tests nuevos: `test_activates_subscription_when_partial_scope_declared_explicitly`
  (5 PVGs, 2 mapeados → active + [info] con 3 grupos), y
  `test_unmapped_provider_groups_do_not_appear_in_tenant_queries`
  (3 PVGs, 1 mapeado → get_provider_tariff devuelve solo el grupo mapeado).

Pendiente para futuro:
- Mecanismo opcional de "warn me when provider launches a new group I haven't seen"
  (alerta proactiva, no bloqueo).
- Interfaz para añadir mappings a una subscription activa sin re-onboardear.

---

## Vehicle group attributes — Phases 1–4

**Goal.** Persist vehicle display attributes (`example_models`, `seats`, `luggage`, `transmission`) that providers expose on their search-result pages alongside each vehicle group, so the SaaS layer can surface them to tenants without an extra scrape.

**What was built.**

*Phase 1 — Schema + repository.*
- Alembic migration `a1b2c3d4e5f6`: adds `example_models TEXT NOT NULL` (server_default `''` for existing rows, default then dropped), `seats INT NULL`, `luggage INT NULL`, `transmission VARCHAR(16) NULL` to `provider_vehicle_groups`.
- `ProviderVehicleGroup` ORM model: 4 new `Mapped` columns.
- `ProviderVehicleGroupRepository.upsert_seen`: extended with `example_models: str` (required), `seats/luggage/transmission: Optional`. On insert: all four persisted. On update: each field overwritten only if changed.
- DATA_MODEL.md pseudo-DDL updated.
- 3 new repository tests (`test_upsert_seen_persists_group_attributes`, `test_upsert_seen_updates_group_attributes_when_changed`, `test_upsert_seen_accepts_null_optional_attributes`).

*Phase 2 — `Car` domain model.*
- `Car` dataclass (`src/shared/domain/models/result.py`): added `example_models: str` (required, 4th positional field), `seats: Optional[int]`, `luggage: Optional[int]`, `transmission: Optional[str]`.
- `RateFilter`: passes the 4 new fields through when reconstructing a `Car`.
- Scrapers updated to pass the four new attributes:
  - `provider_c`: real parsing implemented in Phase 3 (see below).
  - `provider_b`: real parsing implemented (see subsequent entry below).
  - `provider_a`: still passes `example_models=""` as placeholder; parsing not yet implemented.
- All test fixtures updated.

*Phase 3 — provider_c parsing.*
- `provider_c_scraper.py`: parses all 4 attributes from the provider's HTML.
  - `example_models` ← `h3.brxe-sjwbok` text (same element already used for `model`).
  - `seats` ← `[title="Número de plazas"]`; "5+2" → 7 (parts summed), empty → `None`.
  - `luggage` ← `[title="Capacidad maletero"]`; pure digits → int, volumes ("6m³") or empty → `None`.
  - `transmission` ← `[title*="Cambio"]`; "automático" in title → `"automatic"`, else `"manual"`.
- Added `_parse_seats` and `_parse_luggage` as standalone helpers.
- `tests/test_provider_c_scraper.py`: 12 unit tests covering both helpers and all edge cases.

*Phase 4 — Orchestrator wiring.*
- `SmartScraperOrchestrator`: `probe_groups: List[str]` replaced by `probe_cars: Dict[str, Car]` (first Car seen per group from probe results).
- `_persist_zones`: iterates `probe_cars.items()` and passes `car.example_models/seats/luggage/transmission` to `upsert_seen` — attributes populated from the probe pass, not just on extraction.
- `_persist_observations`: passes all 4 attributes from the extraction `car` to `upsert_seen`, keeping them up to date across runs.

**Decisions taken.**

- **`example_models` required at the DB level, but `""` is the sentinel for scrapers not yet migrated.** The DB constraint (NOT NULL) is satisfied; the business constraint ("empty = not yet scraped, not acceptable for a mature provider") is enforced at onboarding time, not in the scraper loop. `provider_a` passes `""` until its parsing is implemented; `provider_b` and `provider_c` parse real values.
- **`seats` "5+2" → 7.** The fold-out seats are summed into the total. The field stores total passenger capacity, not just fixed-seat count.
- **`luggage` in m³ → `None`.** Van cargo volumes ("6m³", "12m³") are incommensurable with bag counts. Rather than force a misleading integer, the field is left null for volume-based providers.
- **`transmission` derived from title attribute, not class.** Class names in the provider's HTML are Bricks Builder-generated and may change. The `title` attribute ("Cambio manual", "Cambio automático") is the human-readable label — more stable and more semantic.
- **First Car per group wins in `probe_cars`.** Multiple probe searches may return the same group; taking the first is cheap and sufficient. Attributes for a group do not differ across searches for the same provider session.
- **Attributes updated in-place on every `upsert_seen`.** If the provider renames a model list between scrapes, the next run overwrites the old value. No history is kept for attribute changes — they are display metadata, not pricing data.

**Closure.** `pytest tests/` passes (97/97). Alembic head at `a1b2c3d4e5f6`.

---

### provider_b — Vehicle group attributes (real parsing)

*Context.* After the "Vehicle group attributes" work above, `provider_b` still passed `example_models=""` as a placeholder. Real parsing was added in a follow-up session using the provider's live results HTML.

*Changes.*
- `provider_b_scraper.py`:
  - `example_models` ← `span.nombregrupo` text (same element already used for `model`).
  - `seats` ← `.icono_container.persona .valor`; plain integer string → `int`, absent icon → `None`.
  - `luggage` ← `.icono_container.maleta .valor` (first match); plain integer → `int`, absent → `None`.
  - `transmission` ← CSS class presence: `.icono_container.manual` → `"manual"`, `.icono_container.automatico` → `"automatic"`, neither → `None`.
  - Added `_parse_int` helper: `int(text.strip())` with `ValueError/AttributeError` → `None`.
- `tests/test_provider_b_scraper.py` (new): 7 unit tests for `_parse_int` covering plain integers, whitespace, empty string, non-numeric text, and float strings.

*Decisions taken.*
- **`_parse_int` does not handle "5+2" sums.** provider_b displays seat counts as plain integers; the helper stays minimal. Summation logic lives in `_parse_seats` (provider_c only).
- **`select_one(".icono_container.maleta .valor")` takes the first match.** Some groups show two bag icons (e.g., small + large). The first icon corresponds to carry-on capacity, which is the more meaningful field for car category comparisons.
- **Groups with no `persona` icon get `seats=None`.** The B1 group uses a `data-seats="0"` attribute instead of the standard icon; `select_one` returns `None` correctly — unknown seats is more honest than zero.

**Closure.** `pytest tests/` passes (104/104).

---

### Observación operacional — Rendimiento dispar de scrapers

*Contexto.* Durante las sesiones de implementación de atributos de grupo de vehículo, se observó una diferencia significativa en el tiempo de extracción por búsqueda entre los scrapers.

| Scraper | Tiempo estimado por búsqueda | Motivo principal |
|---|---|---|
| `provider_c` | ~5 s | Página de resultados carga en una sola petición; sin cambio de pestaña |
| `provider_b` | ~30 s | El formulario abre una nueva pestaña; múltiples esperas de Materialize |

*Implicaciones.*
- Con los 10 durations de extracción actuales (`_DEFAULT_EXTRACTION_DURATIONS`) y un único representative date por zona, una sesión de provider_b con 3 zonas requiere ~15 min solo en extracción.
- provider_c con el mismo escenario tarda ~2,5 min.

*Triggers para optimizaciones diferidas.*
- Esta observación activa **DD-3** (pool de scrapers / paralelismo) y **DD-4** (adaptive probe) de `docs/PRODUCT_SCOPE.md` cuando el volumen de proveedores o localizaciones crezca.
- No se implementa ahora: el pipeline mono-proveedor actual es asumible en entorno de PoC/piloto.

---

## Modelo replantado — Taxonomía canónica como espina dorsal

> Hito en curso. Esta entrada se completa cuando el replanteamiento esté
> implementado y validado. Mientras tanto, sirve como anclaje del **por qué**
> y del **qué cambia**, registrado en el momento de la decisión.

**Goal.** Reorganizar el modelo de datos para que la identidad estable
de "un tipo de vehículo" viva en una taxonomía canónica curada por el
operador, no en el `external_code` de cada provider. Adoptar
clasificación automática vía LLM (Gemini Flash + Pro fallback) durante
el scrape. Renombrar las tablas para reflejar la nueva semántica.

**Contexto del cambio.** El modelo anterior asumía que cada provider
expone un código de grupo estable en su HTML, y construía la identidad
de `provider_vehicle_groups` sobre `(provider, external_code)`. La
realidad observada con providers reales:

- Algunos providers no exponen códigos de grupo en su interfaz pública.
- Al menos un provider los exponía y dejó de hacerlo tras una
  modernización reciente de su web.
- La tendencia del mercado es "menos información técnica visible para
  el usuario", lo que sugiere que el patrón se va a debilitar más,
  no a estabilizarse.

Esto convierte la estrategia anterior de "mapping manual de
provider_groups a client_groups" en frágil: para providers que no
exponen códigos, el scraper tendría que inventar identificadores
sintéticos potencialmente inestables (porque los modelos representativos
mostrados pueden rotar entre scrapes), y el histórico se rompería.

La taxonomía canónica resuelve el problema en su raíz: la identidad
estable es la categoría canónica, no el código del provider. Los
external_codes, cuando existen, se conservan como metadatos
documentales pero no son parte de la estructura.

**What changes (estructural).**

- **Tabla nueva** `canonical_vehicle_types`: maestra de la taxonomía
  del operador. Fuente de verdad: `taxonomy.yaml` versionado en git,
  aplicado a BD por un seed script idempotente. Categorías típicas:
  `ECONOMY_PASSENGER`, `COMPACT_PASSENGER`, `MID_SUV`, `LUXURY_AUTO`,
  `COMMERCIAL`, `MOTORCYCLE`. La taxonomía es deliberadamente coarse
  (10-15 categorías) y estable.
- **Tabla renombrada** `provider_vehicle_groups` → `provider_vehicle_categories`.
  Identidad nueva: `(provider_id, location_id, rate_id, canonical_type_id)`.
  Columnas nuevas: `canonical_type_id` (FK, nullable),
  `classification_confidence` (float, nullable),
  `classification_taxonomy_version` (int, nullable),
  `pending_review` (boolean, default false). Columnas `external_code` y
  `external_name` pasan a ser opcionales y documentales.
- **Tablas renombradas** (capa tenant, alineación léxica con `tenant_*`):
  - `client_vehicle_groups` → `tenant_vehicle_groups`.
  - `vehicle_group_mappings` → `tenant_vehicle_group_mappings`. Ahora
    apunta a `canonical_vehicle_types`, no a `provider_vehicle_groups`.
  - Columnas `client_vehicle_group_id` → `tenant_vehicle_group_id` en
    todas las tablas afectadas.
- **FK renombradas** en `homogeneous_zones`, `price_observations`,
  `price_observation_heartbeats`:
  `provider_vehicle_group_id` → `provider_vehicle_category_id`.
- **Capa tenant ahora es opcional.** Un tenant que no declara
  `tenant_vehicle_groups` consume el producto en lenguaje canónico
  directamente.

**What changes (comportamiento del scraper).**

- Cada vehículo extraído se clasifica en una categoría canónica antes
  de persistirse en `provider_vehicle_categories`.
- Clasificación inline durante el scrape vía interfaz abstracta
  `ClassificationService` (inversión de dependencia: ningún módulo del
  producto se acopla a un proveedor LLM concreto).
- Implementación primaria: Gemini Flash. Fallback condicional: Gemini
  Pro cuando Flash devuelve confianza < 0.85. Umbral hardcoded en
  código.
- Si ambos modelos quedan por debajo del umbral: fila se persiste con
  `canonical_type_id=NULL` y `pending_review=true`. El LLM nunca crea
  categorías nuevas autónomamente.
- Si el LLM falla (timeout, rate limit, error de red): se reutiliza la
  clasificación previa cacheada para esa fila (vía atributos hash o
  external_code); si no existe fila previa, se persiste con
  `pending_review=true`. El scrape sigue.
- Caché de clasificaciones embebida en la propia tabla
  `provider_vehicle_categories` vía `classification_taxonomy_version`.
  Cuando esa versión coincide con la versión actual de la taxonomía,
  la clasificación se reutiliza sin llamar al LLM.

**What changes (gestión de la taxonomía).**

- Fuente de verdad: `taxonomy.yaml` en raíz del repo, versionado en git.
- Cada cambio incrementa `taxonomy_version`. El seed script:
  - Inserta categorías nuevas.
  - Actualiza descripción/criterios cuando cambian.
  - Marca como `active=false` categorías deprecadas (nunca borra).
- Re-clasificación selectiva: filas en categorías deprecadas, filas con
  `pending_review=true`, o filas explícitamente marcadas vía
  `reclassify_on_seed: true` en el YAML. Resto se preserva.

**Migration strategy.**

BD limpia (no migración con datos): borrar las 33 filas existentes en
`provider_vehicle_groups` (Solcar + Victoria) junto con sus zonas,
observations, heartbeats y scrape_runs. El siguiente scrape repuebla
todo con el modelo nuevo y la clasificación automática poblada.

Esta decisión es viable porque no hay clientes en producción todavía y
los datos actuales son material de PoC, no histórico operacional.

**What also changes (consultas).**

- `PriceQueryService` adapta sus tres métodos para usar
  `canonical_vehicle_types` como ejes de fila por defecto.
- Cuando el tenant tiene `tenant_vehicle_groups` declarados, el output
  se etiqueta con sus labels (resolviendo via
  `tenant_vehicle_group_mappings`); cuando no, se etiqueta con códigos
  canónicos.
- N:M policy `min` aplica en dos niveles ahora:
  - Dentro de un provider (cuando varios grupos clasifican en la misma
    categoría).
  - Entre providers en consultas agregadas.

**Decisions taken.**

- **Identidad de provider_vehicle_categories es `(provider, canonical_type)`**,
  no `(provider, external_code)`. external_code documental, no estructural.
- **Capa tenant opcional.** Tenant sin `tenant_vehicle_groups` consume
  el producto. La capa propia es opt-in para clientes que quieran su
  lenguaje.
- **Taxonomía gruesa, no fina.** 10-15 categorías. Si dos grupos del
  provider caen en la misma categoría, se agregan; eso es decisión
  consciente, no bug. Tenants que necesiten granularidad la expresan
  en su propia capa.
- **`tenant_vehicle_group_mappings` apunta a `canonical_vehicle_types`,
  no a `provider_vehicle_categories`.** El cliente mapea contra el
  lenguaje del operador, no contra cada provider individual. Reduce
  drásticamente el número de mappings y aísla al cliente del catálogo
  de cada provider.
- **Full IA con inversión de dependencia.** `ClassificationService`
  como interfaz abstracta. Gemini Flash + Pro como implementación;
  cualquier otro proveedor LLM es swappable.
- **Umbral de confianza 0.85 hardcoded en código.** No `.env`, no BD.
  Cambiar el umbral requiere cambio de código deliberado.
- **BD limpia, no migración con datos.** Viable hoy porque no hay
  clientes en producción. Después de v0, cualquier cambio similar
  requerirá migración con datos preservados.
- **Provider_b también tiene parsing real**, descubierto durante el
  enriquecimiento de atributos. Documentado en la entrada anterior
  "provider_b — Vehicle group attributes (real parsing)". Provider_a
  sigue siendo el único con placeholder `example_models=""`.

**Deferred.**

- **Mecanismo de "explícitamente ignorado"** para categorías canónicas
  que el operador no quiere mostrar a ningún tenant. No necesario en
  v0 — la taxonomía es curada, no incluye categorías que no se quieran
  mostrar.
- **Re-clasificación masiva por cambio de modelo LLM** (cuando se
  migre del proveedor LLM primario a otro distinto). Por ahora se
  asume que clasificaciones hechas con Gemini siguen válidas cuando
  llegue otro modelo.
- **Vista operacional para el operador sobre filas con `pending_review=true`.**
  Necesaria a medio plazo para que la revisión manual no se acumule
  en la sombra. No bloquea v0.
- **Política de aggregación N:M configurable por mapeo** sigue
  diferida (ya estaba antes); cuando llegue, vivirá en
  `tenant_vehicle_group_mappings`.

**Implementation plan.**

1. Diseño de la taxonomía canónica por el operador. Trabajo de
   pensamiento, no de programación. Output: `taxonomy.yaml` con
   10-15 categorías y los 33 grupos actuales (Solcar + Victoria)
   pre-clasificados manualmente para validar que la taxonomía cubre
   el dataset real.
2. Migration Alembic: crear `canonical_vehicle_types`, renombrar
   `provider_vehicle_groups` → `provider_vehicle_categories` con las
   columnas nuevas, renombrar `client_*` → `tenant_*`, renombrar FKs.
3. Seed script idempotente que aplica `taxonomy.yaml` a BD.
4. Implementación del `ClassificationService` (interfaz abstracta +
   Gemini Flash + Gemini Pro fallback).
5. Refactor de scrapers (`upsert_seen` y `_persist_zones`) para usar
   la nueva clave de identidad y llamar al `ClassificationService`.
6. Refactor de `PriceQueryService` para usar
   `canonical_vehicle_types` como ejes de fila.
7. Refactor de todos los tests afectados.
8. Actualizar `CLAUDE.md` y `README.md` con la nueva semántica.
9. BD limpia + scrape de validación end-to-end.

**Notas operacionales.**

- La entrada anterior "Observación operacional — Rendimiento dispar
  de scrapers" hacía referencia a códigos `DD-3` y `DD-4` que existían
  en una versión anterior de `PRODUCT_SCOPE.md`. Tras la consolidación
  de "Decisiones diferidas" en ese documento, esas referencias quedan
  ambiguas. La intención original era apuntar a "Pool de scrapers /
  paralelismo" y a las optimizaciones diferidas de
  `SCRAPING_OPTIMIZATIONS.md`. La nota se mantiene en el log por
  fidelidad histórica; el lector debe interpretar el contenido, no
  los códigos.

**Closure.** Implementación completada en cuatro prompts incrementales
a Claude Code más tres sub-fixes:

- **Prompt 1.** Migration `b1c2d3e4f5a6_replant_canonical_taxonomy_model.py`
  con BD limpia, rename de tablas (`provider_vehicle_groups` →
  `provider_vehicle_categories`, `client_*` → `tenant_*`), creación
  de `canonical_vehicle_types`, ajuste de FKs y columnas nuevas en
  PVC (canonical_type_id, classification_confidence,
  classification_taxonomy_version, pending_review, fuel_type).
  Modelos SQLAlchemy adaptados. Tests del repositorio reescritos.
- **Sub-fix 1.** Reescritura de `_resolve_mappings` en
  `PriceQueryService` con la semántica nueva de tres capas
  (tenant_vehicle_group → canonical_type → PVC). 13 tests del
  servicio actualizados.
- **Sub-fix 2.** Defensive check en `step_create_mappings` del
  onboarding: lanza `OnboardingError` claro cuando un PVC referenciado
  por external_code aún no tiene clasificación canónica. 3 tests
  rotos arreglados, 1 test nuevo.
- **Prompt 2.** Script `scripts/seed_taxonomy.py` idempotente que
  aplica `taxonomy.yaml` a BD. 10 tests del seed.
- **Prompt 3.** `ClassificationService` como interfaz abstracta
  (application layer) más implementación `GeminiClassificationService`
  (Gemini Flash + Pro fallback con threshold 0.85). 12 tests
  unitarios con cliente Gemini mockeado.
- **Prompt 4.** Wiring del scraper: `upsert_seen` adaptado para
  llamar al `ClassificationService` y persistir clasificación;
  composition root del scraper instancia el servicio; tests del
  orchestrator usando `StubClassificationService`.
- **Sub-fix 3.** `external_name` en `provider_vehicle_categories`
  pasa a NULLABLE en BD (estaba NOT NULL pese a que el modelo lo
  declaraba nullable). Migration adicional + commit `7317cc7`.

**Estado final post-bloque.** 141 tests verdes, BD con schema nuevo
y 15 canonical_vehicle_types poblados vía seed real.

**Validación end-to-end pendiente.** El primer scrape real reveló
que la política de agregación intra-provider estaba mal modelada
(dos grupos del mismo provider con precios distintos pueden
clasificar igual canónicamente y eso es información, no ruido).
Eso abre el siguiente hito.

---

## Reversión de la política de agregación intra-provider

> Hito en curso. Este es el aprendizaje más caro del bloque anterior —
> caro en sentido conceptual, no de tiempo: una decisión arquitectónica
> tomada antes de tener datos reales se revela incorrecta al primer
> contacto con un scrape de verdad.

**Goal.** Cambiar la política de agregación intra-provider en el
modelo de datos: pasar de "varios grupos del provider con la misma
canonical category se colapsan en una sola fila de
`provider_vehicle_categories`" a "cada grupo del provider mantiene
su propia fila; la agregación, si se desea, se aplica en query".

**Cómo surgió.** Al ejecutar el primer scrape real tras los 4 prompts
del bloque anterior, Solcar (provider_c) lanzó un error de violación
de constraint unique:

```
duplicate key value violates unique constraint
"uq_provider_vehicle_categories_canonical"
DETAIL: Key (provider_id, provider_location_id, provider_rate_id,
  canonical_type_id)=(1432, 1359, 1329, 162) already exists.
```

Gemini había clasificado dos grupos distintos del mismo provider en
la misma categoría canónica (`INTERMEDIATE_AUTO`):

- Grupo EA (Peugeot 2008, Opel Astra, 5p/4m, auto, 57€/día).
- Grupo GA (Kia XCeed Hybrid, 5p/4m, auto, 69€/día).

La constraint `UNIQUE (provider, location, rate, canonical_type_id)`,
diseñada precisamente para garantizar "una fila por canonical en cada
tupla", saltó.

**Por qué la política original era incorrecta.** La inspección del
caso real abrió una pregunta más profunda: ¿por qué Solcar separa EA
y GA si semánticamente son ambos crossovers automáticos? Porque
cuesta cobrar 12€/día más por uno que por otro. **Los providers crean
tantos grupos como tiers de precio quieren distinguir.** Su
estructura de grupos *es* su estructura de pricing. Colapsar dos
grupos en una sola fila destruye exactamente esa información, que es
la que el producto del cliente necesita para tomar decisiones.

El razonamiento original al adoptar la política de agregación
intra-provider era: "si Gemini clasifica dos grupos como el mismo
canonical, semánticamente son el mismo coche; aplicamos `min` en
persistencia y simplificamos el modelo". El razonamiento estaba
contaminado por una asunción no examinada: que la taxonomía
canónica de 15 categorías era tan fina como la del provider más
fino del mercado. Falso. La taxonomía canónica es deliberadamente
coarse (decisión consciente para que sea estable y reusable entre
providers). Los providers reales segmentan más fino. Tratar de
forzar su catálogo dentro del nuestro es la dirección incorrecta:
debemos respetar su estructura y aplicar agregación solo cuando un
consumidor la pida.

**Decisiones nuevas.**

- **Identidad de `provider_vehicle_categories`:** vuelve a ser
  `(provider, location, rate, external_code)` cuando hay
  `external_code`, o `(provider, location, rate, attributes_hash)`
  cuando no. `canonical_type_id` deja de participar en la identidad
  — es metadato de clasificación.
- **Múltiples filas con la misma `canonical_type_id`** dentro del
  mismo provider son válidas y esperadas. La constraint partial
  unique sobre `(provider, location, rate, canonical_type_id)`
  desaparece.
- **Política `min` intra-provider se mueve a query-time.** El
  `PriceQueryService` aplica `GROUP BY canonical_type_id` con
  `MIN(price_per_day)` al servir tarifarios. La persistencia
  preserva la heterogeneidad completa.
- **Clasificación batch por provider, no vehículo a vehículo.** El
  `ClassificationService` recibe el catálogo completo del provider
  con precios representativos de 7 días para cada grupo. El LLM
  puede así razonar sobre la jerarquía interna del provider y
  distribuir los grupos en categorías canónicas adyacentes en vez
  de colapsarlos. Esto reduce el número de llamadas (~1 por
  provider, no ~N por vehículo) y mejora la calidad de la
  clasificación.
- **Precio representativo de 7 días** se computa como media de los
  precios observados durante el probe phase. Transient: usado solo
  como input al LLM, no persistido.
- **Reclasificación de un provider** se dispara en tres
  situaciones: grupo nuevo en el catálogo, cambio de
  `taxonomy_version`, o comando manual del operador.

**Documentación actualizada.**

- `DATA_MODEL.md`: Decisión 1 reescrita; nueva sección "Within-
  provider heterogeneity: faithfully preserved"; Decisión 2 ajustada
  para reflejar caché por provider; Decisión 3 con la nueva
  semántica de PVC; schema (Part 2) con la nueva identidad; Part 3
  con la query nueva (CTE adicional + `GROUP BY` + `MIN`).
- `PRODUCT_SCOPE.md`: secciones "Clasificación automática",
  "Heterogeneidad intra-provider" (nueva), y "Política N:M de
  agregación (en query, no en persistencia)" reescritas.
- Este `MILESTONES.md`: documentado aquí.

**Lecciones explícitas para futuras decisiones.**

- **Pre-validar contra datos reales antes de cristalizar políticas
  de agregación.** Hubiera bastado mirar los precios de Solcar de
  EA y GA antes de adoptar la política de agregación intra-provider.
  El dato estaba en el repositorio (`provider_vehicle_groups` pre-
  migración tenía precios accesibles vía joins con
  `price_observations`). No se miró.
- **Una taxonomía canónica que pretenda ser estable debe ser
  deliberadamente más coarse que la del provider más fino.** El
  pensamiento "voy a hacer la taxonomía suficientemente fina para
  que cada grupo del provider caiga en una sola categoría" lleva
  a una taxonomía que explota cada vez que se onboardea un provider
  nuevo. La dirección correcta es la contraria: taxonomía coarse,
  heterogeneidad respetada en persistencia, agregación en query.
- **Probar el scrape end-to-end ANTES de cerrar el bloque.** El
  bloque anterior se cerró con "141 tests verdes" pero los tests
  no detectaron la colisión porque no había datos reales en la BD.
  Los tests cubrían lógica de clasificación y persistencia pero no
  el caso real de "dos grupos del mismo provider con la misma
  canonical". Tener un mecanismo de "smoke test integrador" antes
  de cerrar hitos grandes habría detectado esto.

**What's NOT changing.**

- `canonical_vehicle_types` y `taxonomy.yaml`: sin cambios.
- `ClassificationService` como interfaz abstracta: se mantiene; lo
  que cambia es su firma (`classify_provider_batch` en vez de
  `classify`).
- `tenant_vehicle_groups` y `tenant_vehicle_group_mappings`: sin
  cambios estructurales. Los mappings siguen apuntando a canonicals.
- `PriceQueryService` interface pública: sin cambios. El cliente
  pide tarifarios y los recibe; solo cambia la implementación
  interna (añade `GROUP BY` + `MIN`).
- `homogeneous_zones`, `price_observations`,
  `price_observation_heartbeats`: sin cambios estructurales.

**Implementation plan (siguiente bloque de prompts).**

1. Migration nueva que cambia la identidad de
   `provider_vehicle_categories`: drop de la constraint
   `uq_provider_vehicle_categories_canonical`; create de la nueva
   `uq_provider_vehicle_categories_external_code` parcial.
2. Refactor del `ClassificationService`: nueva firma batch
   (`classify_provider_batch`). Implementación Gemini adaptada
   al prompt nuevo (catálogo completo del provider con precios
   representativos).
3. Refactor del flujo del orchestrator: tras el probe phase,
   calcular precio representativo medio de 7 días por cada grupo
   descubierto, llamar al `ClassificationService` una vez para
   todo el provider, persistir resultados antes de la extracción.
4. Refactor de `upsert_seen`: identidad por external_code/hash;
   `canonical_type_id` se setea desde el resultado del batch
   classification, no por llamada inline.
5. Refactor de `PriceQueryService`: añadir `GROUP BY canonical_type_id`
   con `MIN(price_per_day)` en la query principal.
6. BD limpia y re-ejecución del scrape end-to-end.
7. Tests adaptados en cada paso.

**Closure.** Completado. Commit `91cb753`. 146 tests verdes. Migration
`e7f8a9b0c1d2` en cabeza. La BD tiene 15 categorías v1 activas;
el primer scrape real con clasificación batch revelará si alguna de ellas
solapa con v2 (veremos en el siguiente hito de seed).

---

## Taxonomy v2 — 79 categorías canónicas agrupadas en 40 familias

**Goal.** Evolucionar la taxonomía de 15 categorías coarse a 79 categorías
con granularidad real (sedán 7 tiers, SUV 5 tiers, offroad/family/MPV/camper/
van/coupé/convertible/moto). Añadir el campo `family` al modelo de datos para
permitir queries como "rango de precio de la familia COMPACT_SUV".

**Por qué ahora.** Las 15 categorías v1 eran un punto de partida deliberadamente
coarse. Tras el primer scrape real con clasificación automática, se confirmó que
un provider tipico tiene entre 8 y 14 grupos, varios de los cuales caen en la
misma categoría v1 incluso cuando el LLM dispone de contexto de precio. La v2
resuelve esto aumentando la granularidad a 79 categorías, organizadas en 40
familias semánticas (cada familia = mismo tier dentro del mismo body type,
diferenciado por transmisión manual/automática).

**Decisiones tomadas.**

- **79 categorías, 40 familias.** La taxonomía cubre sedán/hatchback (7 tiers),
  SUV compacto/estándar/premium/lujo/ejecutivo (5 tiers), offroad, caravanas,
  furgonetas de pasajeros, MPV, coupé, descapotable y moto. Los códigos siguen
  el patrón `FAMILY_VARIANT` (e.g. `COMPACT_SUV_AUTO`, `COMPACT_SUV_MANUAL`).
- **`family` como columna en BD, no como join a tabla propia.** El campo es
  metadata estructural estable (no cambia entre versiones de forma aleatoria);
  no merece una tabla propia. Una columna indexada es suficiente para
  `GROUP BY family` en queries de precio.
- **`family` no entra en el contexto del LLM.** El clasificador trabaja con
  `code`, `description`, `criteria`, y `examples`. `family` es una agrupación
  para el sistema, no una pista semántica para el modelo.
- **`DEFAULT ''` permanente en BD.** Categorías v1 existentes reciben `family=''`
  hasta que el seed las actualice o las deprece. La constraint NOT NULL se
  satisface con el default; no hay filas inválidas en ningún momento.
- **`taxonomy_version=2` en YAML.** El seed marca como `active=false` las
  categorías que no aparecen en el YAML actual. De las 15 v1, las que también
  estén en v2 (p.ej. `ECONOMY_MANUAL`, `ECONOMY_AUTO`, `COMPACT_MANUAL`,
  `COMPACT_AUTO`, `INTERMEDIATE_MANUAL`, `INTERMEDIATE_AUTO`, `STANDARD_MANUAL`,
  `STANDARD_AUTO`) se actualizan con el nuevo `family`; el resto se deprecan.

**What was built.**

- Alembic migration `f8a9b0c1d2e3`: `ADD COLUMN family VARCHAR(64) NOT NULL DEFAULT ''`
  + índice `ix_canonical_vehicle_types_family`.
- `CanonicalVehicleType` ORM: campo `family` con `default=""` (Python-side)
  para que tests existentes no requieran cambios.
- `CanonicalVehicleTypeRepository.upsert`: parámetro `family: str = ""`; se
  persiste en insert y se actualiza en update.
- `seed_taxonomy.py`: lee `family` del YAML (`cat.get("family", "")`) y lo
  incluye en la detección de cambios (`existing.family != family`).
- Tests en `test_seed_taxonomy.py`:
  - `_cat()` helper extendido con `family: str = ""`.
  - `TestSeedPersistsFamily` (3 tests): insert con family, update de family,
    YAML sin campo family → default `""`.
  - `TestSeedRealTaxonomy` (1 test): seed del `taxonomy.yaml` real verifica
    79 categorías v2 activas y 40 familias distintas.
- `test_parses_real_taxonomy_yaml` en `test_gemini_classification_service.py`
  actualizado a `version=2, len(specs)=79`.

**Deferred.**

- **Seed real contra BD de producción.** El comando `python scripts/seed_taxonomy.py`
  deprecará las v1 sin equivalente en v2 y actualizará el `family` de las que
  sigan vigentes. No se ejecuta aquí — es operación del operador.
- **Re-clasificación de PVCs existentes.** Las PVCs ya clasificadas con
  categorías v1 que no existan en v2 quedarán con `canonical_type_id` apuntando
  a filas `active=false`. La reclasificación se dispara en el siguiente scrape
  con `classify_provider_batch` (que recibe las 79 categorías activas).
- **Queries `BY FAMILY`.** El índice existe; el `PriceQueryService` todavía
  no expone ningún endpoint agrupado por `family`. Diferido hasta que el
  product scope lo incluya explícitamente.

**Closure.** 150 tests verdes (146 anteriores + 4 nuevos). Migration
`f8a9b0c1d2e3` en cabeza de Alembic.

---

## Hito C — ACRISS como estándar de clasificación

**Motivation.** La taxonomía interna (`canonical_vehicle_types`, 79 categorías v2,
campo `family`) resolvía el problema de homogeneización pero introducía un
vocabulario propio que diverge del estándar de la industria. El estándar ACRISS
(4 caracteres: categoría + carrocería + transmisión + combustible) es el código
que los GDS, brokers y operadoras ya utilizan. Adoptar ACRISS elimina la capa
de traducción y facilita integraciones futuras.

**Key decisions.**

- **Subconjunto materializado de 26 códigos.** No se implementa la tabla completa
  de ACRISS (>2000 combinaciones posibles). Se declara explícitamente en
  `acriss_codes.yaml` cuáles aplican a nuestro mercado, con `display_name`,
  `description`, `criteria` y `examples` para el LLM.
- **Columna generada `acriss_code`.** En `provider_vehicle_categories` los 4
  atributos (`acriss_category`, `acriss_body_type`, `acriss_transmission`,
  `acriss_fuel`) se almacenan individualmente. La columna
  `acriss_code VARCHAR(4) GENERATED ALWAYS AS (...) STORED` los concatena;
  la FK `acriss_code → acriss_codes.code` garantiza integridad referencial
  sin duplicar datos.
- **`TenantVehicleGroupMapping.canonical_type_id` → `acriss_code VARCHAR(4)`.**
  El join de precio (`PriceQueryService._resolve_mappings`) ahora cruza
  `pvc.acriss_code == tvgm.acriss_code` en lugar de `pvc.canonical_type_id == tvgm.canonical_type_id`.
- **`upsert_seen` simplificado.** Se eliminan los parámetros `transmission` y
  `fuel_type` del repositorio. El hash de identidad usa solo
  `(example_models, seats, luggage)`.
- **`GeminiClassificationService` recibe `acriss_types: list[AcrissCodeSpec]`.**
  Ya no existe `taxonomy_version`. `ClassificationResult` tiene cuatro campos
  `acriss_*` en lugar de `canonical_type_code`.
- **`seed_taxonomy.py` supersedido.** El workflow de seed es ahora
  `scripts/seed_acriss_codes.py` + `acriss_codes.yaml`. La tabla
  `canonical_vehicle_types` se conserva para referencias históricas de FK
  pero no se usa en el pipeline de clasificación.

**What was built.**

- Alembic migration (Hito C): crea tabla `acriss_codes` con 26 filas seeded;
  drop de `canonical_type_id`, `classification_taxonomy_version`, `transmission`,
  `fuel_type` de `provider_vehicle_categories`; drop de `canonical_type_id` de
  `tenant_vehicle_group_mappings`; añade `acriss_code` FK en ambas tablas;
  columna generada `acriss_code STORED` en `pvc`.
- `AcrissCode` ORM + `AcrissCodeRepository` (`get_by_code`, `list_active`,
  `upsert`, `deactivate_missing`).
- `acriss_codes.yaml` con 26 códigos documentados para el LLM.
- `scripts/seed_acriss_codes.py` — seed idempotente con `--dry-run` y validación.
- `src/saas/application/classification/acriss_loader.py` — carga `AcrissCodeSpec`
  desde YAML para pasarlos a `GeminiClassificationService`.
- `src/scraper/presentation/cli/container.py` actualizado para usar
  `load_acriss_specs` + `acriss_types=` en lugar de `load_taxonomy_specs`.
- Tests actualizados: `test_classification_service.py`, `_fakes.py`,
  `test_gemini_classification_service.py`, `test_repositories.py`,
  `test_price_query_service.py`, `test_onboarding.py`,
  `test_classification_to_query_flow.py`.
- Tests nuevos: `test_acriss_code_repository.py` (14 tests),
  `test_seed_acriss_codes.py` (13 tests).
- `test_seed_taxonomy.py` eliminado (script obsoleto).

**Deferred.**

- **Re-clasificación de PVCs existentes.** Los PVCs clasificados con
  `canonical_type_id` en producción necesitan un scrape con el nuevo
  `ClassificationService` para obtener códigos ACRISS.
- **Onboarding CLI.** El comando de onboarding necesita actualizar su flujo de
  `step_create_mappings` para PVCs ya clasificados en producción.

**Closure.** 161 tests verdes (150 anteriores + 14 nuevos en
`test_acriss_code_repository.py` + 13 nuevos en `test_seed_acriss_codes.py`,
neto de 16 eliminados de `test_seed_taxonomy.py`). Hito C cerrado.
