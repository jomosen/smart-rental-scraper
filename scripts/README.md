# scripts/ — operational maintenance scripts

One-off but repeatable operations against the DB or the recipe catalog. They
all read the connection from the environment (`.env` = dev). To run one
against **prod**, load `.env.prod` and open the SSH tunnel first — same
pattern as `deploy/run_scraper_prod.ps1` (never point them at prod casually).

| Script | What it does |
| --- | --- |
| `clean_provider_scrape_data.py <code> [--yes]` | Deletes a provider's scraped data (observations, heartbeats, zones) so its price series can restart clean. Keeps the catalog: provider, locations, rates, classified vehicle categories, scrape_runs. Dry-run by default. |
| `reprobe_refine.py <code> [--location NAME]` | Re-runs refine discovery (in_place / edit-search deep-link) on an existing active recipe and saves a new version only if a strategy is confirmed by a real date change. No LLM rebuild of the recipe. |
| `reclassify_compare.py [code] [--engine]` | Re-classifies active vehicle groups and diffs against persisted classifications. Default: the configured Gemini models (validate a model upgrade). `--engine`: the deterministic ACRISS engine v2, no LLM (shadow run before flipping the classifier). Writes nothing. |

Conventions:
- Parametrize by `providers.code` — never hardcode a provider.
- Destructive scripts default to dry-run and require `--yes`.
- Scripts live here when they are operations on *state* (DB/recipes).
  Experiments and builders live in `experiments/`; deployment/runtime helpers
  live in `deploy/`.
