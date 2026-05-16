"""Abstract classification service (Port). Implementations live in infrastructure."""
from abc import ABC, abstractmethod

from .dtos import ClassificationResult, VehicleClassificationInput


class ClassificationService(ABC):
    """Classify a provider's vehicle catalog into canonical types in one batch call.

    The batch API gives the LLM full price-tier context so it can distinguish
    groups like "Grupo EA at €57" vs "Grupo GA at €69" that both map to
    INTERMEDIATE_AUTO.

    Flash/Pro fallback strategy: if ANY vehicle in the batch returns confidence
    below 0.85 (and is not already pending_review from an unknown code), the
    entire batch is re-sent to Pro.  Unknown-code results do not trigger Pro —
    they become pending_review immediately.

    Implementations must NOT raise on classification failure; instead return
    pending_review=True results.  May raise on misconfiguration (missing API
    key, empty taxonomy).
    """

    @abstractmethod
    def classify_provider_batch(
        self,
        provider_code: str,
        vehicles: list[VehicleClassificationInput],
    ) -> list[ClassificationResult]:
        """Classify an entire provider's vehicle catalog in a single call.

        Returns one ClassificationResult per input vehicle, in the same order.
        """
        ...
