"""Abstract classification service (Port). Implementations live in infrastructure."""
from abc import ABC, abstractmethod

from .dtos import ClassificationResult, VehicleAttributes


class ClassificationService(ABC):
    """Classify a vehicle's observed attributes into a canonical type.

    Implementations are responsible for:
      - Calling the underlying classifier (LLM or otherwise).
      - Handling fallback strategies.
      - Returning pending_review=True when confidence is insufficient.
      - Returning pending_review=True (with a previously cached
        classification if available) when the underlying call fails.

    The threshold for confidence is implementation-defined but should
    match the project policy (currently 0.85, see DATA_MODEL.md
    Decision 1).

    The list of canonical types and the current taxonomy_version are
    injected at construction time, not passed per call. This decouples
    the service from the DB session and allows test instantiation
    without a live database.
    """

    @abstractmethod
    def classify(self, attributes: VehicleAttributes) -> ClassificationResult:
        """Classify a single vehicle.

        Implementations must NOT raise on classification failure;
        instead return a result with pending_review=True.

        May raise on misconfiguration (missing API key, empty taxonomy)
        — these are operator errors, not runtime classification issues.
        """
        ...
