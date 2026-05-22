"""Playwright browser session — async context manager."""
from __future__ import annotations

import asyncio
from playwright.async_api import async_playwright, Browser, BrowserContext, Page


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class BrowserSession:
    """Manages a single Playwright Chromium session."""

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None

    async def __aenter__(self) -> "BrowserSession":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        self._context = await self._browser.new_context(
            locale="es-ES",
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )
        self.page = await self._context.new_page()
        return self

    async def __aexit__(self, *_) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def navigate(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(1)

    async def wait_for_selector(self, selector: str, timeout_ms: int = 8_000) -> bool:
        """Wait for any element matching *selector* to appear. Returns True if found."""
        try:
            await self.page.wait_for_selector(selector, timeout=timeout_ms)
            return True
        except Exception:
            return False

    async def get_html(self) -> str:
        return await self.page.content()

    async def is_visible(self, selector: str, selector_type: str) -> bool:
        """Return True if the element matching *selector* is currently visible."""
        try:
            if selector_type == "css":
                locator = self.page.locator(selector).first
            else:
                locator = self.page.locator(f"xpath={selector}").first
            return await locator.is_visible()
        except Exception:
            return False

    async def type_text(self, selector: str, text: str, selector_type: str = "css") -> None:
        """Click, clear, then type *text* character-by-character (fires key events)."""
        if selector_type == "css":
            locator = self.page.locator(selector).first
        else:
            locator = self.page.locator(f"xpath={selector}").first
        await locator.click()
        await locator.fill("")
        await locator.press_sequentially(text, delay=50)

    async def get_inner_html(self, selector: str) -> str:
        """Return the inner HTML of the first element matching *selector*."""
        try:
            return await self.page.locator(selector).first.inner_html(timeout=3_000)
        except Exception:
            return ""

    async def get_all_texts(self, selector: str) -> list[str]:
        """Return text contents of all elements matching *selector*."""
        try:
            return await self.page.locator(selector).all_text_contents()
        except Exception:
            return []

    async def click_nth(self, selector: str, index: int) -> bool:
        """Click the Nth element (0-based) matching *selector*."""
        try:
            loc = self.page.locator(selector).nth(index)
            await loc.wait_for(state="visible", timeout=3_000)
            await loc.click(timeout=3_000)
            return True
        except Exception:
            return False

    async def click_option_by_text(
        self, container: str, item_selector: str, text: str
    ) -> bool:
        """Click the first item matching *item_selector* whose text contains *text*.
        Uses item_selector globally (not chained inside container) to avoid
        CSS scoping issues with absolute selector paths."""
        try:
            loc = self.page.locator(item_selector).filter(has_text=text).first
            await loc.wait_for(state="visible", timeout=3_000)
            await loc.click(timeout=3_000)
            return True
        except Exception:
            return False

    async def wait_ms(self, ms: int) -> None:
        await asyncio.sleep(ms / 1000)

    async def scroll_container(self, selector: str, delta_y: int = 200) -> None:
        """Scroll a container element down by *delta_y* pixels (not the page)."""
        try:
            await self.page.evaluate(
                """([sel, dy]) => {
                    const el = document.querySelector(sel);
                    if (el) el.scrollTop += dy;
                }""",
                [selector, delta_y],
            )
        except Exception:
            pass

    def get_url(self) -> str:
        """Return the current page URL."""
        return self.page.url

    async def click_selector(self, selector: str, selector_type: str) -> bool:
        """Click element by CSS or XPath selector. Returns True on success."""
        try:
            if selector_type == "css":
                locator = self.page.locator(selector).first
            else:
                locator = self.page.locator(f"xpath={selector}").first

            await locator.wait_for(state="visible", timeout=5_000)
            await locator.click(timeout=5_000)
            await asyncio.sleep(1)
            return True
        except Exception:
            return False
