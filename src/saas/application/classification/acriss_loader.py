"""Load ACRISS code specs from acriss_codes.yaml for the classification service."""
from __future__ import annotations

from pathlib import Path

import yaml

from src.saas.infrastructure.classification.gemini_service import AcrissCodeSpec


def load_acriss_specs(yaml_path: Path) -> list[AcrissCodeSpec]:
    """Read acriss_codes.yaml and return a list of AcrissCodeSpec objects.

    Raises ValueError if the YAML is malformed or missing required keys.
    """
    parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    raw = parsed.get("acriss_codes", parsed) if isinstance(parsed, dict) else parsed
    if not isinstance(raw, list):
        raise ValueError(
            f"Invalid acriss_codes YAML at {yaml_path}: expected 'acriss_codes' list."
        )

    specs: list[AcrissCodeSpec] = []
    for entry in raw:
        code = entry.get("code", "")
        if len(code) != 4:
            raise ValueError(
                f"Invalid ACRISS code '{code}' in {yaml_path}: must be exactly 4 characters."
            )
        specs.append(AcrissCodeSpec(
            code=code,
            display_name=str(entry.get("display_name", code)),
            description=str(entry.get("description", "")).strip(),
            criteria=list(entry.get("criteria", [])),
            examples=list(entry.get("examples", [])),
        ))

    return specs
