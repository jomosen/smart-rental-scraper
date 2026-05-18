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
        # Brief settle for JS-rendered banners
        await asyncio.sleep(2)

    async def get_html(self) -> str:
        return await self.page.content()

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
