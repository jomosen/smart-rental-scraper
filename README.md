# smart-rental-scraper

Rent-a-car competitor price monitoring system.

> **Status:** PoC validated, in transition to multi-tenant SaaS. The scraping engine is production-ready and persists results directly to Postgres. The SaaS layer (API, multi-tenancy, pricing engine) is in early stages. See `docs/MILESTONES.md` for the build log and `docs/ROADMAP_ARCHITECTURE.md` for where it's headed.

The scraper extracts rates across a date range and rental durations from one or more rent-a-car providers. It uses a smart probing strategy that minimises browser sessions by detecting price seasons and only scraping the unique representative dates of each season, rather than every day in the period.

Provider identities are kept in `providers.json` (gitignored). The committed codebase refers to scrapers generically as `provider_a`, `provider_b`, `provider_c`. No real provider name appears in code or commits.

---

## Repository layout

This is a monorepo. Two sibling modules + a shared domain:

```
src/
├── shared/        Domain models shared across modules. No external dependencies, no I/O.
│   └── domain/models/   BookingSearch, BookingResult, Car, Rate, HomogeneousZone…
├── scraper/       Scraping engine. Clean architecture. Depends only on shared/.
│   ├── domain/          Interfaces and re-exports from shared/.
│   ├── application/     Use cases: smart_scraping/, factories/, filters/, models/.
│   ├── infrastructure/  Playwright driver, concrete scrapers (provider_a, b, c).
│   └── presentation/    CLI entry point, composition root (container.py).
└── saas/          Future SaaS backend.
    ├── application/         catalog_sync, orchestration of persistence.
    └── infrastructure/
        └── persistence/     SQLAlchemy models, sessions, repositories, engines.

migrations/        Alembic migrations (owned by saas/).
deploy/postgres/   Docker init scripts (creates app roles on first BD boot).
docs/              ROADMAP_ARCHITECTURE.md, DATA_MODEL.md, MILESTONES.md, SCRAPING_OPTIMIZATIONS.md.
tests/             Unit + integration tests.
```

**Import rule:** `src/scraper/` and `src/saas/` are sibling modules. Neither imports from the other. Both may import from `src/shared/`. `src/shared/` imports from neither.

Operational guidance for working on the repo (including with Claude Code) lives in `CLAUDE.md` at the root.

---

## Providers configuration

Copy `providers.json.example` to `providers.json` and fill in the entries. The file is gitignored.

```json
[
  {
    "name": "Display name shown in logs and DB",
    "scraper": "provider_a",
    "base_url": "https://...",
    "location_id": "ALC",
    "location_name": "Pickup office display name",
    "rate_name": "Rate name to filter on",
    "enabled": true
  }
]
```

| Field | Description |
|---|---|
| `name` | Display name. Persisted to `providers.display_name` on first scrape. |
| `scraper` | Scraper key. One of `provider_a`, `provider_b`, `provider_c`. Maps to the registry in `container.py`. |
| `base_url` | Entry-point URL for that provider. |
| `location_id` | Internal location identifier expected by the provider. |
| `location_name` | Pickup office display name. |
| `rate_name` | Rate name to filter on. Must match what the scraper returns. |
| `enabled` | Set to `false` to skip this provider without removing it. |

The catalog (`providers`, `provider_locations`, `provider_rates`) is auto-created in the database on the first scrape via `CatalogSyncService`. There is no manual seeding step.

---

## How the scraper works

Three phases run per `(provider, location, rate)` tuple. When multiple tuples are configured, they are scraped in parallel (one browser session per tuple).

```
Phase 1 — Probe
  Weekly 7-day searches across the full period.
  Detects season boundaries by comparing daily prices between consecutive weeks.

Phase 2 — Analysis
  SeasonAnalyzer groups probe results into HomogeneousZones (date ranges where price
  doesn't vary more than SEASON_PRICE_THRESHOLD). Each zone gets a representative date.
  Zones are persisted via HomogeneousZoneRepository.replace_zones_for_tuple — a total
  replacement: previous zones for that tuple are flagged active=false, new ones are
  inserted as active=true. Atomic per (tuple, vehicle_group).

Phase 3 — Extraction
  One search per (representative_date × duration). Searches are deduplicated across
  vehicle groups — a single search returns all groups in one go. Results are persisted
  via PriceObservationRepository.insert_if_changed: a new row is inserted only if the
  price varies relative to the last recorded observation by more than
  PRICE_CHANGE_THRESHOLD. The corresponding heartbeat is upserted unconditionally.
```

Each tuple's run is wrapped in a `scrape_run` row (status: running → success/failed) for traceability.

**Synthetic data is not stored.** Prices for non-representative dates within a zone are derived at read time by joining `homogeneous_zones` with `price_observations`. This keeps the database storing only signal (real measurements) and avoids drift if the analyzer changes. The read-time derivation will live in a future `PriceQueryService`.

### Intra-session fallback

`BaseScraper.scrape_session` reuses a single browser session across all searches:

- First search (or after any failure): `_submit_form` — full page load and form fill.
- Subsequent searches: `_refine_form` — modifies dates from the results page.
- If all attempts for a request fail: one automatic retry via `_submit_form`.

This is why the smart-scraping pipeline is cheap: the cost of opening a browser and authenticating is amortised over many searches.

---

## Configuration

### Pipeline constants (`src/scraper/presentation/cli/main.py`)

| Constant | Default | Description |
|---|---|---|
| `PERIOD_DAYS` | `90` | Period length in days from start. |
| `PERIOD_OFFSET_DAYS` | `2` | Days from today to period start. |
| `PICKUP_HOUR` | `10` | Pickup time, hour of day. |

### Environment variables (`.env`)

The full list is in `.env.example`. The most relevant groups:

| Variable | Purpose |
|---|---|
| `SEASON_PRICE_THRESHOLD` | Minimum relative price change to detect a season boundary (`SeasonAnalyzer`). Default `0.05`. |
| `PRICE_CHANGE_THRESHOLD` | Minimum relative variation for `insert_if_changed` to insert a new observation. Default `0.005`. |
| `POSTGRES_*` | Postgres credentials and host for the docker-compose stack (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT`). |
| `DATABASE_URL` | Legacy connection URL using the superuser. Kept for compatibility. |
| `ADMIN_DATABASE_URL` | Used by Alembic. Connects as `smart_rental_admin` (table owner). |
| `APP_DATABASE_URL` | Used by application code at runtime. Connects as `smart_rental_app` (RLS-enforced). |
| `SUPER_DATABASE_URL` | Used for administrative operations that need cross-tenant access. Connects as `smart_rental_super` (`BYPASSRLS`). |

Whenever `.env.example` gains a new variable, your local `.env` does **not** get updated automatically. Sync new variables manually after pulling.

---

## Local development setup

### Prerequisites

- Python 3.10+
- [Docker](https://docs.docker.com/get-docker/) with Compose v2 (`docker compose`).

### First-time setup

```bash
# 1. Create your local .env from the template
cp .env.example .env

# 2. Start Postgres
docker compose up -d postgres

# 3. Confirm the container is healthy (wait ~10s on first boot)
docker compose ps
# Expected: postgres   running (healthy)

# 4. Verify all four roles were created by the init script
docker compose exec postgres psql -U smart_rental -d smart_rental -c "\du"
# Expected roles: smart_rental, smart_rental_admin, smart_rental_app, smart_rental_super

# 5. Install Python dependencies
pip install -r requirements.txt
playwright install chromium

# 6. Apply database migrations
alembic upgrade head

# 7. Configure providers
cp providers.json.example providers.json
# Edit providers.json with real values (gitignored)
```

### Running the scraper

```bash
python -m src.scraper.presentation.cli.main
```

The scraper requires Postgres to be running and migrations to be applied. It auto-creates the catalog rows from `providers.json` on first run.

### Inspecting results

Results live in Postgres. To peek during development:

```bash
docker compose exec postgres psql -U smart_rental -d smart_rental
```

```sql
SELECT COUNT(*) FROM providers;
SELECT COUNT(*) FROM homogeneous_zones WHERE active = true;
SELECT COUNT(*) FROM scrape_runs ORDER BY started_at DESC LIMIT 5;
SELECT COUNT(*) FROM price_observations;
SELECT COUNT(*) FROM price_observation_heartbeats;
```

### Teardown

```bash
docker compose stop        # stops the container, data volume preserved
docker compose down        # removes the container, data volume preserved
docker compose down -v     # removes the container AND deletes the data volume
```

The `-v` flag is destructive: it wipes all scraped data and forces the init scripts to re-run on next boot. Needed when the role-creation script in `deploy/postgres/init/` changes; otherwise avoid it.

---

## Tests

```bash
pytest tests/
```

Unit tests (no external dependencies):

- `tests/test_season_analyzer.py`
- `tests/test_price_point_extractor.py`

Integration tests against the local Postgres (require Postgres running and migrations applied):

- `tests/saas/infrastructure/persistence/test_repositories.py` — repository contracts.
- `tests/saas/application/test_catalog_sync.py` — auto-creation of catalog rows.
- `tests/saas/application/test_orchestrator_persistence.py` — orchestrator end-to-end with mocked scrapers.

The integration tests use real Postgres with rollback-based isolation; they don't pollute the database between runs. The tests for tenant isolation (RLS) are part of "done" for any feature touching tenant-scoped tables.

---

## Adding a new scraper

1. Create `src/scraper/infrastructure/scrapers/provider_X_scraper.py` inheriting from `BaseScraper`. Read `BaseScraper` first; do not assume hook names.
2. Register it in `SCRAPER_REGISTRY` inside `src/scraper/presentation/cli/container.py` with a key (`provider_d`, `provider_e`…).
3. Add the corresponding entry shape to `providers.json.example`.
4. Never hardcode the real provider name in source files, commit messages, or comments. Use the generic key.

---

## Rent-a-car pricing model

Most operators define prices along three dimensions:

```
Season (date range)
  └── Vehicle category
        └── Duration bracket (1, 2, 3, 4, 5, 6, 7, 14, 21, 28 days)
              └── Price/day
```

For durations not in the bracket table (e.g. 10 days), the typical calculation is:
price of the lower bracket (7 days) + extra days × average daily price of that bracket.

**When a booking spans two seasons**, operators use different strategies:

| Option | Criterion | Pro | Con |
|---|---|---|---|
| A | Price of the pickup season for all days | Predictable for the customer | Incentivises early pickup |
| B | Weighted price by days in each season | Economically accurate | Can produce unexpected price jumps |
| C | Price of the cheaper season | Customer-friendly | Margin loss on bookings crossing into high season |

Option A is the most common among independent operators.

---

## Where to look next

- **`CLAUDE.md`** — operational rules for working on this repo (dependency direction, naming conventions, what not to do, commands).
- **`docs/ROADMAP_ARCHITECTURE.md`** — vision for the SaaS evolution. Read before proposing any structural change.
- **`docs/DATA_MODEL.md`** — canonical schema and modeling decisions (10 decisions, pseudo-DDL, anatomy of the main query, deferred items).
- **`docs/MILESTONES.md`** — log of what's been built and when. Entry point if you're returning to the project after a break.
- **`docs/SCRAPING_OPTIMIZATIONS.md`** — deferred scraping optimisations (adaptive probe, layered frequency) with their re-evaluation triggers.

---

## Future direction: Universal AI scraper

The current architecture requires a manual scraper per provider. The proposed evolution replaces provider-specific scrapers with a two-layer universal engine.

**Layer 1 — Universal AI engine (~90% of providers).** A multimodal model (vision + HTML) discovers form fields, fills them step by step reacting to the real page state, and extracts results semantically — no hardcoded CSS selectors.

**Layer 2 — Specific adapters (~10% of providers).** For complex flows: CAPTCHAs, heavily custom interactions. Equivalent to current scrapers, only for edge cases.

A recipe cache persists the action sequence from each successful run. In normal production, the model is only called when a new provider is added or an existing recipe breaks — cost is per provider, not per search.

Tools like Stagehand and Playwright MCP follow a similar approach. The implementation would only affect the infrastructure layer; domain and application layers remain unchanged.
