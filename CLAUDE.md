# CLAUDE.md

Operational briefing for Claude Code working in this repo.
Read this fully before any non-trivial change.

---

## What this repo is

`smart-rental-scraper` — a rent-a-car competitor price scraper.
PoC stage, validated. Next phase: evolve into a multi-tenant SaaS pricing engine.

- **Architectural vision for the SaaS evolution lives in `docs/ROADMAP_ARCHITECTURE.md`.** Read it before proposing any structural change (multi-tenancy, API surface, persistence, deployment).
- **Database schema and data-model decisions live in `docs/DATA_MODEL.md`.** This is the source of truth for tables, relationships, indexes, and modeling decisions. Do not change the schema without updating that document first.
- The committed codebase **never names real providers**. Use `provider_a`, `provider_b`, `provider_c` only. Real names live in `providers.json` (gitignored).

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
```

If you add a dependency, update `requirements.txt`. Do not introduce a new package manager (poetry, uv, etc.) without asking.

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

1. Create `src/scraper/infrastructure/scrapers/provider_X_scraper.py` inheriting from `BaseScraper`.
2. Implement the Template Method hooks defined by `BaseScraper` (`_submit_form`, `_refine_form`, `_parse_results`, etc. — read `BaseScraper` first, do not assume the hook names).
3. Register it in `SCRAPER_REGISTRY` inside `src/scraper/presentation/cli/container.py` with a new key (`provider_d`, `provider_e`…).
4. Add the corresponding entry shape to `providers.json.example`.
5. **Never** hardcode the real provider name in source files, commit messages, or comments. Use the generic key.

Reuse `_refine_form` for date-only changes within an existing session; `_submit_form` is for the first request and for recovery after failures. Do not collapse them — the session reuse is what keeps the scrape cheap.

---

## Smart-scraping pipeline (do not break the contract)

Three phases run per provider:

1. **Probe** (`SeasonProbe`) — weekly 7-day searches across the period.
2. **Analysis** (`SeasonAnalyzer`) — groups probe results into `HomogeneousZone`s using `SEASON_PRICE_THRESHOLD`. Zones are persisted to `seasons/{provider}.json`.
3. **Extraction** — one search per (representative date × duration). Searches are deduplicated across car groups; a single search returns all groups.

After extraction, `ResultExpander` fills remaining days from each zone's representative date. **Expanded prices are synthetic.** When persisting or exporting, expanded points must remain distinguishable from real scraped points (`is_synthetic` flag in the SaaS model — see `docs/DATA_MODEL.md` §6).

Do not add scraping calls inside the expansion step. The whole point of the design is that expansion is free.

---

## Testing

- Tests live in `tests/`, run without a browser.
- Existing coverage: `test_season_analyzer.py`, `test_price_point_extractor.py`, `test_result_expander.py`.
- When you add or modify logic in `scraper/application/smart_scraping/` or `scraper/application/services/`, add or update tests in the same PR. These are the modules with the most reasoning logic — they need coverage.
- Do **not** add tests that require a real browser session or hit real provider URLs. Anything `infrastructure/playwright/` or `infrastructure/scrapers/` is integration-tested manually for now.
- When the SaaS database layer lands, **tenant-isolation tests are part of "done"**, not optional. See `docs/DATA_MODEL.md` §8.

---

## Configuration

- **Per-provider config** → `providers.json` (gitignored). Template in `providers.json.example`.
- **Global runtime tunables** → `.env` (e.g. `SEASON_PRICE_THRESHOLD`, `PRICE_CHANGE_THRESHOLD`).
- **Pipeline constants** (period length, pickup hour, spot-check count) → top of `src/scraper/presentation/cli/main.py`.

When adding a new tunable, decide deliberately which of the three layers it belongs to and document it in the README config tables.

---

## Outputs

Files are written with a timestamp suffix (`_<ts>`). The set is:

- `results_<ts>.csv/json` — raw scraped results (representative dates).
- `results_expanded_<ts>.csv` — full daily coverage, expanded.
- `seasons_<ts>.csv/json` — detected zones per car group.
- `seasons_unified_<ts>.csv/json` — unique extraction dates across all groups.
- `gaps_<ts>.csv/json` — searches that returned errors or no cars.

If you change an exporter, keep the column set backward-compatible unless the user explicitly asks for a schema change. The client consumes these files downstream.

---

## Things that have bitten us before

- **Reusing the browser session is fragile.** If a refinement fails, the fallback to a fresh `_submit_form` is what keeps the run alive. Don't "simplify" by removing the fallback.
- **Provider name leakage.** Easy to commit a real name in a comment, log line, or test fixture. Grep before committing.
- **Synthetic vs. real prices.** Losing the distinction between scraped and expanded data is the single most damaging silent bug in this domain. If you touch persistence or export, double-check the flag survives.
- **Group-name mismatch across providers.** "Economy" in one provider is not "Economy" in another. The PoC sidesteps this with `rate_name` filtering. The SaaS version solves it explicitly via mapping tables — see `docs/DATA_MODEL.md` §1.
- **The scraper is a separate process from the SaaS.** In the SaaS architecture, the scraper runs as a worker that pulls jobs from the SaaS API. Do not introduce assumptions of always-on, low-latency, in-process connectivity between the scraping pipeline and the database/API. Scraper code talks to the SaaS only through HTTP, and the worker initiates every connection.
- **FORCE RLS means even the table owner is blocked.** The `tenants` table uses `FORCE ROW LEVEL SECURITY`. Any operation that writes to tenant-scoped tables without `app.tenant_id` set (including creating a new tenant) must use the `smart_rental_super` role (`BYPASSRLS`). Use `super_session()` for provisioning; use `tenant_context()` for everything else. Never lower the RLS level to work around a missing session scope.
- **Alembic must run as `smart_rental_admin`, not the superuser.** `migrations/env.py` reads `ADMIN_DATABASE_URL`. If you switch it back to `DATABASE_URL` (the superuser), ownership and GRANT logic in the migrations will silently become a no-op and the app role will lose table access.
- **`.env` vs `.env.example` drift.** When a hito modifies `.env.example` (adding new variables), the user's local `.env` is NOT updated automatically. Tests will skip or fail with confusing errors until the user manually syncs the missing variables from `.env.example` to `.env`. When adding new environment variables, explicitly remind the user to sync their `.env` file.
- **Postgres init scripts only run on database creation.** Files in `deploy/postgres/init/` (mounted to `/docker-entrypoint-initdb.d/`) are executed only when Postgres initializes a new data directory. To apply changes to those scripts, the user must run `docker compose down -v` (note the `-v` flag, which deletes the volume) followed by `docker compose up -d postgres`. The `-v` flag is critical and easy to forget. Migrations have to be re-applied afterwards.

---

## When in doubt

- **Database / data-model questions** (tables, indexes, what to store, relationships, tenant isolation, authentication shape) → `docs/DATA_MODEL.md`. This document is canonical; if a contradiction exists between it and the code, fix the code or update the document explicitly.
- **Architecture / SaaS evolution decisions** (API surface, deployment, multi-tenancy at the application level, scraper/SaaS separation) → `docs/ROADMAP_ARCHITECTURE.md`. If it isn't there, ask before deciding.
- **Scraping performance / scaling concerns** (adaptive probe, layered scrape frequency) → `docs/SCRAPING_OPTIMIZATIONS.md`. These optimizations are **deferred** — do not implement preemptively.
- **Domain reasoning** (seasons, duration brackets, cross-season bookings) → README §"Rent-a-car pricing model".
- **SaaS persistence layer** (engines, sessions, repositories, tenant scoping) → `src/saas/infrastructure/persistence/`. Entry points: `engine.py` (three engines), `session.py` (`tenant_context` / `super_session`), `repositories/` (one file per aggregate root).
- **Anything else not covered here** → ask for clarification rather than guessing. This codebase is small enough that wrong assumptions are cheaper to surface than to undo.
