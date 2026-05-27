"""infrastructure/builder — browser-based recipe discovery and execution.

D1 stub.  The actual implementation lives in experiments/scraper_builder/
until D2, when the infrastructure files (browser_session, cookie_closer,
fillers/, extraction/, results_detection/, scraper_engine, etc.) will be
relocated here and given proper package-qualified imports.

In D1 the runner scripts import directly from experiments/scraper_builder/
(via sys.path) for the browser operations.  The application/builder layer
references them through the executor callable pattern (dependency injection)
so no hard imports from experiments/ appear in src/scraper/.
"""
