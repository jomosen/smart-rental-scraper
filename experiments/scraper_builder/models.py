from dataclasses import dataclass
from typing import Literal


@dataclass
class CloseResult:
    success: bool
    action: Literal["clicked", "no_banner_found", "failed"]
    selector: str | None = None
    selector_type: Literal["css", "xpath"] | None = None
    rationale: str | None = None
    attempts: int = 0
    cost_estimate_eur: float = 0.0
    duration_seconds: float = 0.0
    error: str | None = None


@dataclass
class LLMDecision:
    selector: str | None
    selector_type: Literal["css", "xpath"] | None
    rationale: str
    tokens_input: int
    tokens_output: int
