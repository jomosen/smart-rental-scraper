"""Input/output dataclasses of the ACRISS engine — mirror of spec §13-§14."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

LetterSource = str  # "scraped" | "source_override" | "model_dictionary"
                    # | "name_token" | "heuristic" | "fallback"

ClassificationSource = str  # "source_override" | "official_verified"
                            # | "model_dictionary" | "heuristic" | "mixed"


@dataclass(frozen=True)
class VehicleInput:
    raw_model: str
    transmission: Optional[str] = None
    doors: Optional[int] = None
    seats: Optional[int] = None
    source: Optional[str] = None  # provider code, e.g. "recordgo"


@dataclass(frozen=True)
class NormalizedModel:
    make: Optional[str]
    model: Optional[str]
    variant: Optional[str]
    key: Optional[str]  # dictionary key when resolved (e.g. "volkswagen_golf_variant")


@dataclass(frozen=True)
class LetterResult:
    code: Optional[str]
    name: Optional[str]
    confidence: float
    source: LetterSource
    explanation: str = ""


@dataclass(frozen=True)
class AlternativeResult:
    acriss: str
    confidence: float
    reason: str


@dataclass
class AcrissResult:
    raw_model: str
    normalized: NormalizedModel
    acriss: Optional[str]
    partial_acriss: str  # unknown letters rendered as '?'
    confidence: float    # min() across the four letters (§15)
    category: LetterResult
    type: LetterResult
    transmission: LetterResult
    fuel_power: LetterResult
    classification_source: ClassificationSource
    assumptions: list[str] = field(default_factory=list)
    alternatives: list[AlternativeResult] = field(default_factory=list)
    powertrain_ambiguous: bool = False
    needs_review: bool = False
    # True when the model could not be resolved against the dictionary — the
    # caller may queue it for review / LLM semantic resolution (§31-32).
    model_unresolved: bool = False

    @property
    def letters(self) -> dict[str, LetterResult]:
        return {
            "category": self.category,
            "type": self.type,
            "transmission": self.transmission,
            "fuel_power": self.fuel_power,
        }

    @property
    def explanation(self) -> dict[str, str]:
        return {name: lr.explanation for name, lr in self.letters.items()}

    def to_dict(self) -> dict:
        """JSON-serializable form (persisted into classification_detail)."""
        return {
            "raw_model": self.raw_model,
            "normalized": {
                "make": self.normalized.make,
                "model": self.normalized.model,
                "variant": self.normalized.variant,
            },
            "acriss": self.acriss,
            "partial_acriss": self.partial_acriss,
            "confidence": round(self.confidence, 3),
            "letters": {
                name: {
                    "code": lr.code,
                    "name": lr.name,
                    "confidence": round(lr.confidence, 3),
                    "source": lr.source,
                }
                for name, lr in self.letters.items()
            },
            "classification_source": self.classification_source,
            "assumptions": self.assumptions,
            "alternatives": [
                {"acriss": a.acriss, "confidence": round(a.confidence, 3), "reason": a.reason}
                for a in self.alternatives
            ],
            "powertrain_ambiguous": self.powertrain_ambiguous,
            "needs_review": self.needs_review,
            "explanation": self.explanation,
        }
