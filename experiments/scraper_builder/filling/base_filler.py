"""Abstract base for all location field fillers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from browser_session import BrowserSession
from field_analysis.field_identifier import IdentifiedField
from field_analysis.widget_classifier import WidgetInfo
from filling.form_state import FormStateDiff
from session_logger import SessionLogger


@dataclass
class FillResult:
    success: bool
    strategy_used: str
    target_value: str
    matched_option: str | None
    state_changes: FormStateDiff | None
    duration_seconds: float
    error: str | None


class LocationFiller(ABC):
    @property
    @abstractmethod
    def strategy_name(self) -> str: ...

    @abstractmethod
    async def fill(
        self,
        session: BrowserSession,
        field: IdentifiedField,
        widget: WidgetInfo,
        target_value: str,
        form_selector: str,
        logger: SessionLogger,
    ) -> FillResult: ...
