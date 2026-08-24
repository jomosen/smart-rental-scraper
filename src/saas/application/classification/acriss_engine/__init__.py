"""Deterministic ACRISS classification engine (engine v2).

Public API (§40):
    classify_vehicle(VehicleInput)  -> AcrissResult
    classify_vehicles([...])        -> [AcrissResult]

Pure rules + repo-versioned dictionaries (data/acriss-*.json). No LLM, no I/O
beyond loading the data files. See docs/DATA_MODEL.md Decision 12.
"""
from .classifier import classify_vehicle, classify_vehicles
from .dictionaries import ModelDictionary, load_dictionary, load_source_overrides
from .types import AcrissResult, AlternativeResult, LetterResult, VehicleInput

__all__ = [
    "classify_vehicle",
    "classify_vehicles",
    "VehicleInput",
    "AcrissResult",
    "AlternativeResult",
    "LetterResult",
    "ModelDictionary",
    "load_dictionary",
    "load_source_overrides",
]
