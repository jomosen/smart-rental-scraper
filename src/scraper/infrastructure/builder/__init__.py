"""infrastructure/builder — browser-based recipe discovery and execution.

Playwright-based scraping engine and all supporting infrastructure:
browser session management, cookie handling, form filling, field analysis,
date analysis, extraction, results detection, and recipe execution.

Primary entry points:
  scraper_engine.scrape()          — full LLM-assisted scrape (Fase B)
  recipe_executor.run_recipe()     — recipe-driven scrape (no LLM, Fase D)
"""
