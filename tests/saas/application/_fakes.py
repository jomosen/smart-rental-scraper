"""Shared test doubles for SaaS application-layer tests."""
from __future__ import annotations

from src.saas.application.classification.dtos import ClassificationResult
from src.saas.application.classification.service import ClassificationService


class StubClassificationService(ClassificationService):
    """Deterministic classification: maps external_code → canonical_type_code.

    Useful for tests that don't want to mock per-call. Any external_code not
    in code_map returns pending_review=True with confidence=0.0.

    call_count tracks how many times classify() was invoked.
    """

    def __init__(
        self,
        code_map: dict[str, str],
        taxonomy_version: int = 1,
        default_confidence: float = 0.95,
    ) -> None:
        self._code_map = code_map
        self._taxonomy_version = taxonomy_version
        self._default_confidence = default_confidence
        self.call_count = 0

    def classify(self, attributes) -> ClassificationResult:
        self.call_count += 1
        code = self._code_map.get(attributes.external_code)
        if code is None:
            return ClassificationResult(
                canonical_type_code=None,
                confidence=0.0,
                taxonomy_version=self._taxonomy_version,
                pending_review=True,
            )
        return ClassificationResult(
            canonical_type_code=code,
            confidence=self._default_confidence,
            taxonomy_version=self._taxonomy_version,
            pending_review=False,
        )
