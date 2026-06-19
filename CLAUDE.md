# CLAUDE.md

Operational briefing for Claude Code working in this repo.
Read this fully before any non-trivial change.

---

## What this repo is

`smart-rental-scraper` — a rent-a-car competitor price scraper.
PoC stage, validated. Next phase: evolve into a multi-tenant SaaS pricing engine.

- **Architectural vision for the SaaS evolution lives in `docs/ROADMAP_ARCHITECTURE.md`.** Read it before proposing any structural change (multi-tenancy, API surface, persistence, deployment).
- **Database schema and data-model decisions live in `docs/DATA_MODEL.md`.** This is the source of truth for tables, relationships, indexes, and modeling decisions. Do not change the schema without updating that document first.
- Provider names are now real in the codebase: `victoria`, `solcar`, `centauro`. Real URLs live in the `providers` DB table (never in source). Do not hardcode credentials or scraping internals specific to a provider in commit messages.

---

## Canonical commands

```bash
# Install
pip install -r requirements.txt
playwright install chromium

# Run the full pipeline
python -m src.scraper.presentation.cli.main

# Tests (no browser required)
pytest tests/

# Front-end (RentRadar web UI under src/saas/presentation/web)
npm --prefix src/saas/presentation/web run build   # rebuild dist/ (required to see changes)
npm --prefix src/saas/presentation/web run dev      # Vite dev server with hot reload
```

If you add a dependency, update `requirements.txt`. Do not introduce a new package manager (poetry, uv, etc.) without asking.

**Front-end changes need a build to be visible.** The FastAPI app serves the
**pre-built** bundle from `src/saas/presentation/web/dist/` (`api/app.py`), not the
Vite dev server. Any edit under `web/` (TSX/CSS) only shows up after
`npm --prefix src/saas/presentation/web run build` and a hard browser refresh — or
while running `npm run dev` (Vite, hot reload). Don't expect TSX/CSS edits to appear
just by restarting the API.

---

## Architecture rules

The repo is organized as a monorepo under `src/`:

- **`src/shared/`** — domain models shared across modules (`BookingProvider`, `BookingSearch`, `BookingResult`, `HomogeneousZone`, etc.). No external dependencies, no I/O.
- **`src/scraper/`** — the scraping engine (full clean-architecture stack).
- **`src/saas/`** — future SaaS backend (placeholder). Not yet implemented.

Within `src/scraper/`, the dependency direction is:

```
presentation → application → domain
infrastructure → domain (implements interfaces)
```

Concretely:

- **`scraper/domain/`** has zero external dependencies. Only interfaces and re-exports from `shared/`.
- **`scraper/application/`** orchestrates use cases. Depends on domain interfaces, never on `infrastructure/` directly.
- **`scraper/infrastructure/`** is the only place that touches Playwright, the filesystem, or HTTP. Implements domain interfaces.
- **`scraper/presentation/cli/container.py`** is the **only** place where concrete classes are wired. Composition root pattern. Do not instantiate concrete infrastructure classes anywhere else.

**Import rule:** models from `src/shared/` always use 4-dot relative imports from within `src/scraper/` (e.g. `from ....shared.domain.models.result import BookingResult`).

If a change requires breaking these boundaries, stop and surface the trade-off — don't silently leak dependencies upward.

Database migrations live in `migrations/` at repo root and are managed by Alembic. They are owned by `src/saas/` (the only component that writes to the DB), but the directory is at root for tooling conventions.

---

## Adding a new scraper

When the user asks to add a new provider scraper:

1. Create `src/scraper/infrastructure/scrapers/<provider_name>_scraper.py` inheriting from `BaseScraper`.
2. Implement the Template Method hooks defined by `BaseScraper` (`_submit_form`, `_refine_form`, `_parse_results`, etc. — read `BaseScraper` first, do not assume the hook names).
3. Register it in `SCRAPER_REGISTRY` inside `src/scraper/presentation/cli/container.py` with the provider's `scraper_key`.
4. Insert a row into `providers` (with `scraper_key` matching the registry key and `base_url` set) plus rows in `provider_locations` and `provider_rates` into the local DB. No `providers.json` — the DB is the source of truth.

For recipe-based providers (zero-code): run `run_build_recipe.py` — it calls `ProviderProvisioningService.ensure()` which handles DB setup and saves the recipe atomically.

Reuse `_refine_form` for date-only changes within an existing session; `_submit_form` is for the first request and for recovery after failures. Do not collapse them — the session reuse is what keeps the scrape cheap.

---

## Smart-scraping pipeline (do not break the contract)

Three phases run per (provider, location, rate):

1. **Probe** (`SeasonProbe`) — weekly 7-day searches across the period.
2. **Analysis** (`SeasonAnalyzer`) — groups probe results into `HomogeneousZone`s using `SEASON_PRICE_THRESHOLD`. Zones are persisted via `HomogeneousZoneRepository.replace_zones_for_tuple`: previous zones for the same (provider, location, rate, vehicle_group) tuple are marked `active=false`, the new set is inserted as `active=true`. See `docs/DATA_MODEL.md` §6.
3. **Extraction** — one search per (representative date × duration). Searches are deduplicated across car groups; a single search returns all groups. Results are persisted via `PriceObservationRepository.insert_if_changed`, which only inserts a new `price_observations` row when the price differs from the last recorded observation by more than `PRICE_CHANGE_THRESHOLD`; otherwise it updates the `price_observation_heartbeats` row in place.

Each run creates a `scrape_runs` row on start (`ScrapeRunRepository.create`) and closes it with `mark_finished(status="success"|"failed")` on exit.

**Synthetic data is not persisted.** If the SaaS needs the price for a day that was not scraped, it derives it at read time by crossing `homogeneous_zones` with `price_observations` (logic that will live in a future `PriceQueryService`). Do not add scraping calls inside that price-query layer when it exists — derivation must read from existing observations and zones, not trigger new scrapes.

---

## Testing

- Tests live in `tests/`, run with `pytest tests/`.
- Existing coverage:
  - `tests/test_season_analyzer.py` — unit tests for `SeasonAnalyzer`
  - `tests/test_price_point_extractor.py` — unit tests for `PricePointExtractor`
  - `tests/saas/infrastructure/persistence/test_repositories.py` — integration tests for all repositories (Hito 3)
  - `tests/saas/application/test_orchestrator_persistence.py` — integration tests for `SmartScraperOrchestrator` DB persistence (Hito 4)
- Tests under `tests/saas/` are integration tests against the local Postgres. They require the DB to be running and migrations to be applied. Run `docker compose up -d postgres && alembic upgrade head` before `pytest` if it's not already up.
- When you add or modify logic in `scraper/application/smart_scraping/` or `scraper/application/services/`, add or update tests in the same PR. These are the modules with the most reasoning logic — they need coverage.
- Do **not** add tests that require a real browser session or hit real provider URLs. Anything `infrastructure/playwright/` or `infrastructure/scrapers/` is integration-tested manually for now.
- Tenant-isolation tests are already in place (`TestTenantIsolation` in `test_repositories.py`). Any new tenant-scoped feature must include equivalent isolation coverage.

---

## Configuration

- **Per-provider config** → `providers` DB table (status='active'). Add a new provider by inserting rows into `providers`, `provider_locations`, and `provider_rates`. For recipe-based providers, `run_build_recipe.py` does this automatically.
- **Global runtime tunables** → `.env`. This includes scraping thresholds (`SEASON_PRICE_THRESHOLD`, `PRICE_CHANGE_THRESHOLD`), anti-detection macro-pause settings (`ANTIBOT_BREAK_EVERY_MIN_LOW/HIGH`, `ANTIBOT_BREAK_DURATION_LOW/HIGH` — set `ANTIBOT_BREAK_DURATION_LOW=0` to disable), and the database connection URLs (`ADMIN_DATABASE_URL`, `APP_DATABASE_URL`, `SUPER_DATABASE_URL`, plus `POSTGRES_*` for the docker-compose stack). See `.env.example` for the full list.
- **Pipeline constants** (period length, pickup hour, spot-check count) → top of `src/scraper/presentation/cli/main.py`.

When adding a new tunable, decide deliberately which of the three layers it belongs to and document it in the README config tables.

---

## Outputs

Results are persisted to the database — no CSV or JSON files are written. The relevant
tables are `price_observations`, `homogeneous_zones`, `scrape_runs`, and
`price_observation_heartbeats`. To inspect data during development, connect to the local
Postgres and query those tables directly.

**The scraping pipeline writes no CSV/JSON** (that ban is about the data-sink, not reports).
The SaaS API exposes `GET /api/cross-tariff/export.csv` and `export.pdf` for client-facing
pricing reports; these are read-only exports of already-persisted data and are intentional.
Do not remove them citing the "no CSV output" rule.

---

## Things that have bitten us before

- **Reusing the browser session is fragile.** If a refinement fails, the fallback to a fresh `_submit_form` is what keeps the run alive. Don't "simplify" by removing the fallback.
- **Provider name leakage.** Easy to commit a real name in a comment, log line, or test fixture. Grep before committing.
- **Synthetic vs. real prices.** Losing the distinction between scraped and expanded data is the single most damaging silent bug in this domain. If you touch persistence or export, double-check the flag survives.
- **Group-name mismatch across providers.** "Economy" in one provider is not "Economy" in another. The PoC sidesteps this with `rate_name` filtering. The SaaS version solves it explicitly via mapping tables — see `docs/DATA_MODEL.md` §1.
- **The scraper is a separate process from the SaaS.** In the SaaS architecture, the scraper runs as a worker that pulls jobs from the SaaS API. Do not introduce assumptions of always-on, low-latency, in-process connectivity between the scraping pipeline and the database/API. Scraper code talks to the SaaS only through HTTP, and the worker initiates every connection.
- **FORCE RLS means even the table owner is blocked.** The `tenants` table uses `FORCE ROW LEVEL SECURITY`. Any operation that writes to tenant-scoped tables without `app.tenant_id` set (including creating a new tenant) must use the `smart_rental_super` role (`BYPASSRLS`). Use `super_session()` for provisioning; use `tenant_context()` for everything else. Never lower the RLS level to work around a missing session scope.
- **Alembic must run as `smart_rental_admin`, not the superuser.** `migrations/env.py` reads `ADMIN_DATABASE_URL`. If you switch it back to `DATABASE_URL` (the superuser), ownership and GRANT logic in the migrations will silently become a no-op and the app role will lose table access.
- **Synthetic data is no longer persisted.** `ResultExpander` has been removed. Synthetic prices are derived on read from `homogeneous_zones` via a future `PriceQueryService` (not yet implemented). Anything that tries to persist `is_synthetic=True` observations is a bug.
- **`.env` vs `.env.example` drift.** When a hito modifies `.env.example` (adding new variables), the user's local `.env` is NOT updated automatically. Tests will skip or fail with confusing errors until the user manually syncs the missing variables from `.env.example` to `.env`. When adding new environment variables, explicitly remind the user to sync their `.env` file.
- **Postgres init scripts only run on database creation.** Files in `deploy/postgres/init/` (mounted to `/docker-entrypoint-initdb.d/`) are executed only when Postgres initializes a new data directory. To apply changes to those scripts, the user must run `docker compose down -v` (note the `-v` flag, which deletes the volume) followed by `docker compose up -d postgres`. The `-v` flag is critical and easy to forget. Migrations have to be re-applied afterwards.
- **`base_url` for custom scrapers must be set in DB after migration.** The `j2k3l4m5n6o7` migration adds `providers.base_url` and seeds centauro automatically. For victoria and solcar, the D2.6 migration updates their rows. Without `base_url` set, `VictoriaRentACarScraper`/`SolcarScraper` will navigate to an empty URL on launch.

---

## When in doubt

- **Alcance del producto** (¿está esto dentro de v0?, ¿qué se ha decidido fuera?, ¿cuándo se reabre algo?) → `docs/PRODUCT_SCOPE.md`.
- **Database / data-model questions** (tables, indexes, what to store, relationships, tenant isolation, authentication shape) → `docs/DATA_MODEL.md`. This document is canonical; if a contradiction exists between it and the code, fix the code or update the document explicitly.
- **Architecture / SaaS evolution decisions** (API surface, deployment, multi-tenancy at the application level, scraper/SaaS separation) → `docs/ROADMAP_ARCHITECTURE.md`. If it isn't there, ask before deciding.
- **Scraping performance / scaling concerns** (adaptive probe, layered scrape frequency) → `docs/SCRAPING_OPTIMIZATIONS.md`. These optimizations are **deferred** — do not implement preemptively.
- **Domain reasoning** (seasons, duration brackets, cross-season bookings) → README §"Rent-a-car pricing model".
- **SaaS persistence layer** (engines, sessions, repositories, tenant scoping) → `src/saas/infrastructure/persistence/`. Entry points: `engine.py` (three engines), `session.py` (`tenant_context` / `super_session`), `repositories/` (one file per aggregate root).
- **Anything else not covered here** → ask for clarification rather than guessing. This codebase is small enough that wrong assumptions are cheaper to surface than to undo.
