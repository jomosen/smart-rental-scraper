from abc import ABC, abstractmethod


class IPageNavigator(ABC):
    """Lifecycle and navigation: launch, navigate, load state, close."""

    @abstractmethod
    async def launch(self, headless: bool = True) -> None:
        """Launches the browser."""
        ...

    @abstractmethod
    async def navigate(self, url: str) -> None:
        """Navigates to the given URL."""
        ...

    @abstractmethod
    async def wait_for_load_state(self, state: str = "networkidle", timeout: int = 30000) -> None:
        """Waits until the page reaches the given load state.
        States: 'load', 'domcontentloaded', 'networkidle'."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Closes the browser and releases resources."""
        ...
