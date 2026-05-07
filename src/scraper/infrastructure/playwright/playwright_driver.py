import random
import asyncio

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from ...domain.interfaces.driver import IBrowserDriver


# Stealth args that disable automation signals in Chromium
_STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1920,1080",
    "--start-maximized",
]

_STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'languages', { get: () => ['es-ES', 'es', 'en'] });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    window.chrome = { runtime: {} };
"""


class PlaywrightDriver(IBrowserDriver):
    """
    IBrowserDriver implementation with anti-detection (stealth) techniques.

    Uses Chromium with args that suppress automation signals and an
    initialisation script that patches navigator.webdriver.
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def launch(self, headless: bool = True) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=headless,
            args=_STEALTH_ARGS,
        )
        context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-ES",
            timezone_id="Europe/Madrid",
            viewport={"width": 1920, "height": 1080},
            java_script_enabled=True,
        )
        await context.add_init_script(_STEALTH_JS)
        self._context = context
        self._page = await context.new_page()

    async def navigate(self, url: str) -> None:
        await self._page.goto(url, wait_until="domcontentloaded")

    async def get_page_source(self) -> str:
        return await self._page.content()

    async def click(self, selector: str, force: bool = False) -> None:
        await self._page.click(selector, force=force)

    async def type_text(self, selector: str, text: str) -> None:
        """Types character by character with a random delay to simulate human typing."""
        await self._page.click(selector)
        for char in text:
            await self._page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.05, 0.15))

    async def wait_for_selector(self, selector: str, timeout: int = 5000, state: str = "visible") -> None:
        await self._page.wait_for_selector(selector, state=state, timeout=timeout)

    async def select_option(self, selector: str, value: str) -> None:
        await self._page.select_option(selector, value=value)

    async def set_input_value(self, selector: str, value: str) -> None:
        """Sets value via JS for readonly inputs and fires change/input events."""
        await self._page.evaluate(
            f"""
            const el = document.querySelector("{selector}");
            el.value = "{value}";
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            """
        )

    async def screenshot(self, path: str) -> None:
        await self._page.screenshot(path=path, full_page=True)

    async def get_attribute(self, selector: str, attribute: str) -> str:
        return await self._page.get_attribute(selector, attribute) or ""

    async def js_click(self, selector: str) -> None:
        """Click via JS: ignores visibility. No-op if the selector does not exist."""
        await self._page.evaluate(
            f'const el = document.querySelector("{selector}"); if (el) el.click();'
        )

    async def click_and_switch_tab(self, selector: str) -> None:
        """Clicks the selector and switches focus to the new tab that opens."""
        async with self._context.expect_page() as new_page_info:
            await self._page.click(selector)
        new_page = await new_page_info.value
        await new_page.wait_for_load_state("domcontentloaded")
        self._page = new_page

    async def element_exists(self, selector: str) -> bool:
        locator = self._page.locator(selector)
        if await locator.count() == 0:
            return False
        return await locator.first.is_visible()

    async def wait_for_load_state(self, state: str = "networkidle", timeout: int = 30000) -> None:
        await self._page.wait_for_load_state(state, timeout=timeout)

    async def scroll_to_top(self) -> None:
        await self._page.evaluate("window.scrollTo(0, 0)")

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
