"""Shared test doubles for SaaS application-layer tests."""
from __future__ import annotations

from src.saas.application.classification.dtos import ClassificationResult, VehicleClassificationInput
from src.saas.application.classification.service import ClassificationService


class StubClassificationService(ClassificationService):
    """Deterministic batch classification: maps external_code → ACRISS 4-char code.

    code_map values must be valid 4-char ACRISS codes (e.g. 'EDMR').
    Any external_code not in code_map returns pending_review=True with confidence=0.0.

    call_count tracks total individual vehicle classifications across all batch calls.
    """

    def __init__(
        self,
        code_map: dict[str, str],  # external_code → 4-char ACRISS code
        default_confidence: float = 0.95,
    ) -> None:
        self._code_map = code_map
        self._default_confidence = default_confidence
        self.call_count = 0

    def classify_provider_batch(
        self,
        provider_code: str,
        vehicles: list[VehicleClassificationInput],
    ) -> list[ClassificationResult]:
        results: list[ClassificationResult] = []
        for vehicle in vehicles:
            self.call_count += 1
            code = self._code_map.get(vehicle.external_code)
            if code is None or len(code) != 4:
                results.append(ClassificationResult(
                    acriss_category=None,
                    acriss_body_type=None,
                    acriss_transmission=None,
                    acriss_fuel=None,
                    confidence=0.0,
                    pending_review=True,
                ))
            else:
                results.append(ClassificationResult(
                    acriss_category=code[0],
                    acriss_body_type=code[1],
                    acriss_transmission=code[2],
                    acriss_fuel=code[3],
                    confidence=self._default_confidence,
                    pending_review=False,
                ))
        return results
