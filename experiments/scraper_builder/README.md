# scraper_builder — Cookie Banner Closer (PoC)

Isolated experiment that uses **Claude Sonnet 4.6** to identify and click the
"accept cookies" button on websites, with no dependency on the product codebase.

## How it works

1. **`BrowserSession`** — launches a headless Playwright Chromium browser and
   navigates to the target URL.
2. **`extract_banner_candidates`** — scores all DOM elements with BeautifulSoup
   heuristics (id/class keywords, `role="dialog"`, cookie-related text) and
   returns the top-3 candidate snippets.
3. **`ask_llm`** — sends the candidates to Claude Sonnet 4.6 and receives a CSS
   or XPath selector targeting the accept/close button.
4. **`close_cookies`** — clicks the selector and verifies the banner is gone.
   Retries up to 3 times if the banner persists.

## Setup

```bash
cd experiments/scraper_builder
pip install -r requirements.txt
playwright install chromium
```

Create a `.env` file (or set the environment variable directly):

```
ANTHROPIC_API_KEY=sk-ant-...
```

## Run

```bash
# headless (default)
python run_experiment.py

# visible browser window
python run_experiment.py --visible
```

## Adding more test sites

Edit `TEST_SITES` in `run_experiment.py`:

```python
TEST_SITES = [
    ("centauro", "https://www.centauro.net"),
    ("mysite",   "https://example.com"),
]
```

## File layout

| File | Role |
|---|---|
| `models.py` | `CloseResult` and `LLMDecision` dataclasses |
| `browser_session.py` | Playwright async context manager |
| `banner_detector.py` | BeautifulSoup heuristic scorer |
| `llm_selector.py` | Anthropic API call + response parsing |
| `cookie_closer.py` | Orchestration loop (navigate → detect → LLM → click) |
| `run_experiment.py` | CLI entry point |
| `requirements.txt` | Isolated dependency list |

## Isolation

This experiment is **not imported** from any product code. It can be deleted or
moved without affecting `src/`.
