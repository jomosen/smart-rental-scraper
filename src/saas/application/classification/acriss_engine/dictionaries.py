"""Model dictionary / aliases / source overrides — loading and lookup.

Data lives in repo-versioned JSON (data/acriss-models.json,
data/acriss-aliases.json, data/acriss-source-overrides.json) — never hardcoded
in engine logic (§18, §39). Matching order (§36): exact key → alias → word
containment (longest key wins) → fuzzy ≥ 0.90. Fuzzy below threshold never
auto-accepts.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from .normalizer import norm_key

_DATA_DIR = Path(__file__).parents[5] / "data"
MODELS_PATH = _DATA_DIR / "acriss-models.json"
ALIASES_PATH = _DATA_DIR / "acriss-aliases.json"
OVERRIDES_PATH = _DATA_DIR / "acriss-source-overrides.json"

FUZZY_ACCEPT = 0.90  # §36


@dataclass(frozen=True)
class ModelMatch:
    entry_id: str
    entry: dict
    via: str          # "exact" | "alias" | "contains" | "fuzzy"
    score: float      # 1.0 except for fuzzy


class ModelDictionary:
    """In-memory index over acriss-models.json + acriss-aliases.json."""

    def __init__(self, models: dict[str, dict], aliases: dict[str, str]) -> None:
        # Drop documentation keys ("_comment") and anything non-entry-shaped.
        models = {
            k: v for k, v in models.items()
            if not k.startswith("_") and isinstance(v, dict)
        }
        aliases = {
            k: v for k, v in aliases.items() if not k.startswith("_")
        }
        self._models = models
        # key (normalized "make model" / alias) → entry_id
        self._index: dict[str, str] = {}
        # model-only key → entry_ids (used when the make is missing)
        self._model_only: dict[str, list[str]] = {}

        for entry_id, entry in models.items():
            full = norm_key(f"{entry['make']} {entry['model']}")
            self._index[full] = entry_id
            model_key = norm_key(entry["model"])
            # Model-only keys must be specific enough to stand alone: a bare
            # "4" (DS 4) or "5" (Omoda 5) would greedily match ANY name
            # containing that digit ("Chery Tiggo 4" is not a DS 4).
            if len(model_key) >= 3 and not model_key.isdigit():
                self._model_only.setdefault(model_key, []).append(entry_id)
            for alias in entry.get("aliases", []):
                self._index.setdefault(norm_key(alias), entry_id)

        for alias, entry_id in aliases.items():
            if entry_id in models:
                self._index.setdefault(norm_key(alias), entry_id)

    def get(self, entry_id: str) -> Optional[dict]:
        return self._models.get(entry_id)

    def lookup(self, normalized_text: str) -> Optional[ModelMatch]:
        text = normalized_text.strip()
        if not text:
            return None

        # 1-2. Exact key / alias match
        entry_id = self._index.get(text)
        if entry_id:
            return ModelMatch(entry_id, self._models[entry_id], "exact", 1.0)

        # 2b. Model-only exact match (make missing), only when unambiguous
        candidates = self._model_only.get(text)
        if candidates and len(candidates) == 1:
            eid = candidates[0]
            return ModelMatch(eid, self._models[eid], "exact", 1.0)

        # 3. Word-bounded containment — the LONGEST contained key wins, so
        #    "volkswagen golf variant" beats "volkswagen golf" when both fit.
        padded = f" {text} "
        best_key, best_id = "", None
        for key, eid in self._index.items():
            if len(key) > len(best_key) and f" {key} " in padded:
                best_key, best_id = key, eid
        if best_id is None:
            for key, eids in self._model_only.items():
                if len(eids) == 1 and len(key) > len(best_key) and f" {key} " in padded:
                    best_key, best_id = key, eids[0]
        if best_id:
            return ModelMatch(best_id, self._models[best_id], "contains", 1.0)

        # 4. Fuzzy — high threshold, never aggressive (§36). Guard: a fuzzy
        #    match must not add/remove body-variant words (Variant/Estate/SW…).
        best_score, best_id = 0.0, None
        for key, eid in self._index.items():
            score = SequenceMatcher(None, text, key).ratio()
            if score > best_score:
                best_score, best_id = score, eid
        if best_id and best_score >= FUZZY_ACCEPT and not _variant_mismatch(
            text, norm_key(f"{self._models[best_id]['make']} {self._models[best_id]['model']}")
        ):
            return ModelMatch(best_id, self._models[best_id], "fuzzy", best_score)

        return None


_VARIANT_WORDS = ("variant", "estate", "sw", "touring", "sportstourer",
                  "wagon", "kombi", "avant", "break")


def _variant_mismatch(a: str, b: str) -> bool:
    """True when exactly one of the two strings carries an estate marker."""
    has = lambda s: any(re.search(rf"\b{w}\b", s) for w in _VARIANT_WORDS)
    return has(a) != has(b)


# ── Loaders (cached per-path) ─────────────────────────────────────────────────

@lru_cache(maxsize=4)
def _load_json(path_str: str) -> Any:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def load_dictionary(
    models_path: Path = MODELS_PATH, aliases_path: Path = ALIASES_PATH
) -> ModelDictionary:
    return ModelDictionary(
        models=_load_json(str(models_path)),
        aliases=_load_json(str(aliases_path)),
    )


def load_source_overrides(path: Path = OVERRIDES_PATH) -> dict[str, dict[str, dict]]:
    return _load_json(str(path))


def find_source_override(
    overrides: dict[str, dict[str, dict]], source: Optional[str], entry_id: Optional[str]
) -> Optional[dict]:
    if not source or not entry_id:
        return None
    return overrides.get(norm_key(source), {}).get(entry_id)
