# smart-rental-scraper

> **Proof of concept** built for a client in the rent-a-car industry who needed to automate competitor price extraction. Manual monitoring of rival rates across seasons, durations, and vehicle categories is time-consuming and error-prone; this PoC validates that the process can be fully automated with minimal browser sessions.

Rent-a-car price scraper for multiple providers. Extracts rates across a date range and rental durations using a smart scraping strategy that minimises the number of browser sessions required, then exports results to CSV and JSON.

## Providers

Provider identities are kept in `.env` (not committed). The code references them generically as Provider A and Provider B.

| Key | Description |
|---|---|
| `PROVIDER_A_NAME` / `PROVIDER_B_NAME` | Display name used in exports |
| `PROVIDER_A_BASE_URL` / `PROVIDER_B_BASE_URL` | Entry-point URL for the booking form |
| `PROVIDER_A_LOCATION_ID` / `PROVIDER_B_LOCATION_ID` | Internal location identifier |
| `PROVIDER_A_LOCATION_NAME` / `PROVIDER_B_LOCATION_NAME` | Display name of the pickup location |
| `PROVIDER_A_RATE_NAME` / `PROVIDER_B_RATE_NAME` | Rate name to filter on (e.g. "Premium") |
| `SEASON_PRICE_THRESHOLD` | Minimum relative price change to detect a season boundary (default `0.05`) |

Copy `.env.example` to `.env` and fill in your values.

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
├── domain/
│   ├── models/             BookingSearch, BookingResult, Car, Rate,
│   │                       Provider, HomogeneousZone, PricePoint, SeasonBoundary
│   └── interfaces/
│       ├── browser/        IPageNavigator, IPageInteractor, IPageReader
│       ├── driver.py       IBrowserDriver (composes the three browser sub-interfaces)
│       ├── scraper.py      IBookingScraper
│       ├── scraper_factory.py  IScraperFactory
│       └── smart_scraping.py  ISeasonProbe, ISeasonAnalyzer,
│                              ISearchPlanBuilder, ISeasonBoundaryRepository
├── application/
│   ├── smart_scraping/     SeasonProbe, SeasonAnalyzer, SearchPlanBuilder,
│   │                       SmartScraperOrchestrator, PricePointExtractor
│   ├── services/           SpotCheckService, ResultExpander, session_runner
│   ├── exporters/          ResultExporter, SeasonExporter, GapExporter
│   ├── filters/            RateFilter
│   ├── factories/          ScraperFactory
│   └── models/             SearchRequest
├── infrastructure/
│   ├── playwright/         PlaywrightDriver (stealth, anti-bot)
│   ├── scrapers/           BaseScraper (Template Method), ProviderAScraper, ProviderBScraper
│   └── repositories/       JsonSeasonBoundaryRepository
├── presentation/
│   └── cli/
│       ├── container.py    Composition root — wires all dependencies
│       └── main.py         Entry point and runtime configuration
tests/
│   ├── test_season_analyzer.py
│   ├── test_price_point_extractor.py
│   └── test_result_expander.py
```

## Configuration

**`main.py`** — runtime constants:

| Variable | Default | Description |
|---|---|---|
| `PERIOD_DAYS` | `90` | Period length in days |
| `PERIOD_OFFSET_DAYS` | `2` | Days from today to period start |
| `PICKUP_HOUR` | `10` | Pickup time (hour) |
| `SPOT_CHECK_COUNT` | `10` | Spot checks per provider (5 real + 5 synthetic) |

To disable a provider, comment out its line in the `providers` list inside `container.py`.

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
python -m src.presentation.cli.main
```

Run tests (no browser required):

```bash
pytest tests/
```

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
