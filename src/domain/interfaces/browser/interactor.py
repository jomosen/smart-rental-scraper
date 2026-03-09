from abc import ABC, abstractmethod


class IPageInteractor(ABC):
    """Form interactions and user gestures."""

    @abstractmethod
    async def click(self, selector: str, force: bool = False) -> None:
        """Clicks the element matching the selector. force=True skips visibility checks."""
        ...

    @abstractmethod
    async def select_option(self, selector: str, value: str) -> None:
        """Selects an option in a <select> by its value attribute."""
        ...

    @abstractmethod
    async def set_input_value(self, selector: str, value: str) -> None:
        """Sets a value on an input via JS (useful for readonly inputs)."""
        ...

    @abstractmethod
    async def js_click(self, selector: str) -> None:
        """Fires a click via JavaScript, ignoring visibility and Playwright checks."""
        ...

    @abstractmethod
    async def click_and_switch_tab(self, selector: str) -> None:
        """Clicks the selector and switches focus to the new tab that opens."""
        ...

    @abstractmethod
    async def scroll_to_top(self) -> None:
        """Scrolls to the top of the page."""
        ...
