"""Load canonical type specs from taxonomy.yaml for the classification service."""
from __future__ import annotations

from pathlib import Path

import yaml

from src.saas.infrastructure.classification.gemini_service import CanonicalTypeSpec


def load_taxonomy_specs(yaml_path: Path) -> tuple[list[CanonicalTypeSpec], int]:
    """Read taxonomy.yaml and return (canonical_type_specs, taxonomy_version).

    All categories listed in the YAML are included; deprecated handling
    (active=false in the DB) is managed by the seed script, not here.
    For LLM prompts we use every category present in the YAML.

    Raises ValueError if the YAML is malformed.
    """
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if "taxonomy_version" not in raw or "categories" not in raw:
        raise ValueError(
            f"Invalid taxonomy YAML at {yaml_path}: missing required keys."
        )

    specs = [
        CanonicalTypeSpec(
            code=c["code"],
            name=c["name"],
            description=str(c["description"]).strip(),
            criteria=list(c.get("criteria", [])),
            examples=list(c.get("examples", [])),
        )
        for c in raw["categories"]
    ]
    return specs, int(raw["taxonomy_version"])
