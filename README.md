# smart-rental-scraper

> **Proof of concept** built for a client in the rent-a-car industry who needed to automate competitor price extraction. Manual monitoring of rival rates across seasons, durations, and vehicle categories is time-consuming and error-prone; this PoC validates that the process can be fully automated with minimal browser sessions.

Rent-a-car price scraper for multiple providers. Extracts rates across a date range and rental durations using a smart scraping strategy that minimises the number of browser sessions required, then exports results to CSV and JSON.

## Providers

Provider identities are kept in `providers.json` (not committed). The code references scrapers generically as Provider A, B, C… — no provider name appears in the committed codebase.

Copy `providers.json.example` to `providers.json` and fill in your values:

```json
[
  {
    "name": "Display name used in exports",
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
| `name` | Display name used in exports |
| `scraper` | Scraper key: `provider_a`, `provider_b`, `provider_c` |
| `base_url` | Entry-point URL |
| `location_id` | Internal location identifier expected by the provider |
| `location_name` | Pickup office display name |
| `rate_name` | Rate name to filter on (must match what the scraper returns) |
| `enabled` | Set to `false` to skip this provider without removing it |

Global settings in `.env`:

| Variable | Description |
|---|---|
| `SEASON_PRICE_THRESHOLD` | Minimum relative price change to detect a season boundary (default `0.05`) |

## How it works

Execution runs in three phases per provider (both providers run in parallel):

```
Phase 1 — Probe
  Weekly 7-day searches across the full period.
  Detects season boundaries by comparing daily prices between consecutive weeks.

Phase 2 — Analysis
  SeasonAnalyzer groups probe results into HomogeneousZones (periods where price
  does not vary more than SEASON_PRICE_THRESHOLD). Each zone gets a representative date.
  Boundaries are persisted asynchronously to seasons/{provider}.json.

Phase 3 — Extraction
  One search per unique representative date × duration [1,2,3,4,5,6,14,21,28 days].
  Searches are deduplicated across car groups — a single search returns all groups.
```

After extraction, `ResultExpander` fills in every remaining day of the period by
copying prices from each zone's representative date. No additional scraping is needed.

### Intra-session fallback

`BaseScraper.scrape_session` reuses a single browser session across all searches:

- First search (or after any failure): `_submit_form` — full page load + form fill.
- Subsequent searches: `_refine_form` — modifies dates from the results page.
- If all attempts for a request fail: one automatic retry via `_submit_form`.

### Spot check

After export, `SpotCheckService` re-scrapes a random sample to verify data integrity:

- 10 checks per provider: 5 from real scraped pairs, 5 from expanded (synthetic) pairs.
- Comparison is exact (zero tolerance).

## Project structure

```
src/
├── shared/                 Shared domain models (imported by both scraper and future saas)
│   └── domain/models/      BookingProvider, BookingSearch, BookingResult, Car, Rate,
│                           HomogeneousZone, PricePoint, SeasonBoundary
├── scraper/                Scraping engine (clean-architecture stack)
│   ├── domain/
│   │   └── interfaces/
│   │       ├── browser/    IPageNavigator, IPageInteractor, IPageReader
│   │       ├── driver.py   IBrowserDriver
│   │       ├── scraper.py  IBookingScraper
│   │       ├── scraper_factory.py  IScraperFactory
│   │       └── smart_scraping.py  ISeasonProbe, ISeasonAnalyzer,
│   │                              ISearchPlanBuilder, ISeasonBoundaryRepository
│   ├── application/
│   │   ├── smart_scraping/ SeasonProbe, SeasonAnalyzer, SearchPlanBuilder,
│   │   │                   SmartScraperOrchestrator, PricePointExtractor
│   │   ├── services/       SpotCheckService, ResultExpander, session_runner
│   │   ├── exporters/      ResultExporter, SeasonExporter, GapExporter
│   │   ├── filters/        RateFilter
│   │   ├── factories/      ScraperFactory
│   │   └── models/         SearchRequest
│   ├── infrastructure/
│   │   ├── playwright/     PlaywrightDriver (stealth, anti-bot)
│   │   ├── scrapers/       BaseScraper (Template Method), ProviderAScraper, ProviderBScraper,
│   │   │                   ProviderCScraper
│   │   └── repositories/   JsonSeasonBoundaryRepository
│   └── presentation/
│       └── cli/
│           ├── container.py  Composition root — wires all dependencies
│           └── main.py       Entry point and runtime configuration
└── saas/                   Future SaaS backend (placeholder)
tests/
├── test_season_analyzer.py
├── test_price_point_extractor.py
└── test_result_expander.py
```

## Configuration

**`main.py`** — runtime constants:

| Variable | Default | Description |
|---|---|---|
| `PERIOD_DAYS` | `90` | Period length in days |
| `PERIOD_OFFSET_DAYS` | `2` | Days from today to period start |
| `PICKUP_HOUR` | `10` | Pickup time (hour) |
| `SPOT_CHECK_COUNT` | `10` | Spot checks per provider (5 real + 5 synthetic) |

To disable a provider without removing it, set `"enabled": false` in `providers.json`.

To add a new provider with an existing scraper, add an entry to `providers.json`. To add a completely new scraper, create a `provider_X_scraper.py` inheriting from `BaseScraper` in `src/scraper/infrastructure/scrapers/` and register it with a new key in `SCRAPER_REGISTRY` inside `src/scraper/presentation/cli/container.py`.

## Outputs

| File | Contents |
|---|---|
| `results_<ts>.csv/json` | Raw scraped results (representative dates only) |
| `results_expanded_<ts>.csv` | Full coverage: every day filled from zone data |
| `seasons_<ts>.csv/json` | Detected zones per car group |
| `seasons_unified_<ts>.csv/json` | Unique extraction dates across all groups |
| `gaps_<ts>.csv/json` | Searches that returned errors or no cars |

## Running

```bash
pip install -r requirements.txt
playwright install chromium
python -m src.scraper.presentation.cli.main
```

Run tests (no browser required):

```bash
pytest tests/
```

## Database setup (local development)

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Compose v2 (`docker compose` — note: no hyphen).

### Steps

```bash
# 1. Create your local .env from the template (defaults work out of the box)
cp .env.example .env

# 2. Start Postgres
docker compose up -d postgres

# 3. Confirm the container is healthy
docker compose ps
# Expected: postgres   running (healthy)

# 4. Install Python dependencies (includes SQLAlchemy, Alembic, psycopg)
pip install -r requirements.txt

# 5. Apply migrations (no-op for now — initial migration is empty)
alembic upgrade head

# 6. Verify the migration is recorded
alembic current
# Expected output: <revision-id> (head)

# 7. Test the round-trip
alembic downgrade base   # reverts to pre-migration state
alembic upgrade head     # re-applies
```

### Teardown

```bash
docker compose down        # stops the container, data volume is preserved
docker compose down -v     # stops the container AND deletes the data volume
```

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

## Future direction: Universal AI scraper

The current architecture requires a manual scraper per provider. The proposed evolution replaces provider-specific scrapers with a two-layer universal engine:

**Layer 1 — Universal AI engine (~90% of providers)**
A multimodal model (vision + HTML) discovers form fields, fills them step by step
reacting to the real page state, and extracts results semantically — no hardcoded
CSS selectors.

**Layer 2 — Specific adapters (~10% of providers)**
For complex flows: CAPTCHAs, heavily custom interactions. Equivalent to current scrapers, only for edge cases.

A recipe cache persists the action sequence from each successful run. In normal production, the model is only called when a new provider is added or an existing recipe breaks — cost is per provider, not per search.

Tools like Stagehand and Playwright MCP follow a similar approach. The implementation would only affect the infrastructure layer; domain and application layers remain unchanged.
