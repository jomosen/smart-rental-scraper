from abc import ABC, abstractmethod


class IPageReader(ABC):
    """Reading page state: source, attributes, element presence, screenshots."""

    @abstractmethod
    async def get_page_source(self) -> str:
        """Returns the HTML of the current page."""
        ...

    @abstractmethod
    async def get_attribute(self, selector: str, attribute: str) -> str:
        """Returns the value of the given attribute from the first element matching the selector."""
        ...

    @abstractmethod
    async def element_exists(self, selector: str) -> bool:
        """Returns True if there is at least one visible element matching the selector."""
        ...

    @abstractmethod
    async def wait_for_selector(self, selector: str, timeout: int = 5000, state: str = "visible") -> None:
        """Waits until the selector is in the given state ('visible', 'attached', 'hidden')."""
        ...
