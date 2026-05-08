"""Onboarding config: dataclasses and YAML loader.

Schema (Option B — mappings live inside subscriptions, not vehicle_groups):

    tenant:
      name: Acme Rentals
      currency: EUR
      owner_email: admin@acme.com

    vehicle_groups:
      - code: compact
        name: Compact
        description: Small cars   # optional
        display_order: 1          # required integer >= 0

    subscriptions:
      - provider_code: provider_a
        location_code: MAD1
        rate_code: standard_rate
        mappings:
          - client_group_code: compact   # must match a vehicle_group code above
            external_codes: [ECMR, CCAR]
      - provider_code: provider_a        # same provider_code, different tuple is allowed
        location_code: BCN1
        rate_code: standard_rate
        mappings:
          - client_group_code: compact
            external_codes: [ECMR]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


class ConfigError(ValueError):
    """Raised when the onboarding YAML config is malformed."""


@dataclass
class MappingEntry:
    client_group_code: str
    external_codes: list[str]


@dataclass
class VehicleGroupConfig:
    code: str
    name: str
    description: Optional[str]
    display_order: int


@dataclass
class SubscriptionConfig:
    provider_code: str
    location_code: str
    rate_code: str
    mappings: list[MappingEntry] = field(default_factory=list)


@dataclass
class TenantConfig:
    name: str
    currency: str
    owner_email: str


@dataclass
class OnboardingConfig:
    tenant: TenantConfig
    vehicle_groups: list[VehicleGroupConfig]
    subscriptions: list[SubscriptionConfig]


def load_config(path: Path) -> OnboardingConfig:
    """Parse and validate a tenant onboarding YAML file."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    _validate(raw)
    return _parse(raw)


# ---------------------------------------------------------------------------
# Internal validation + parsing
# ---------------------------------------------------------------------------

def _validate(raw: object) -> None:
    if not isinstance(raw, dict):
        raise ConfigError("Config must be a YAML mapping at the top level.")

    # tenant section
    t = raw.get("tenant") or {}
    if not str(t.get("name", "")).strip():
        raise ConfigError("tenant.name is required and must be non-empty.")
    currency = str(t.get("currency", "")).strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ConfigError(
            "tenant.currency must be a 3-letter ASCII alphabetic ISO 4217 code (e.g. EUR)."
        )
    email = str(t.get("owner_email", "")).strip()
    if "@" not in email:
        raise ConfigError("tenant.owner_email must contain '@'.")

    # vehicle_groups — parsed first so client_group_codes in subscriptions can be validated
    groups = raw.get("vehicle_groups") or []
    if not groups:
        raise ConfigError("At least one vehicle_group is required.")
    group_codes: set[str] = set()
    for grp in groups:
        gcode = str(grp.get("code", "")).strip()
        if not gcode:
            raise ConfigError("Each vehicle_group must have a non-empty code.")
        if gcode in group_codes:
            raise ConfigError(f"Duplicate vehicle_group code: '{gcode}'.")
        group_codes.add(gcode)
        if not str(grp.get("name", "")).strip():
            raise ConfigError(f"vehicle_group '{gcode}' must have a non-empty name.")
        raw_order = grp.get("display_order")
        if raw_order is None:
            raise ConfigError(
                f"vehicle_group '{gcode}': display_order is required (integer >= 0)."
            )
        try:
            order_int = int(raw_order)
        except (TypeError, ValueError):
            raise ConfigError(
                f"vehicle_group '{gcode}': display_order must be an integer, got {raw_order!r}."
            )
        if order_int < 0:
            raise ConfigError(
                f"vehicle_group '{gcode}': display_order must be >= 0, got {order_int}."
            )

    # subscriptions
    subs = raw.get("subscriptions") or []
    if not subs:
        raise ConfigError("At least one subscription is required.")
    seen_tuples: set[tuple[str, str, str]] = set()
    for s in subs:
        pcode = str(s.get("provider_code", "")).strip()
        lcode = str(s.get("location_code", "")).strip()
        rcode = str(s.get("rate_code", "")).strip()
        if not pcode:
            raise ConfigError("Each subscription must have a non-empty provider_code.")
        if not lcode:
            raise ConfigError(f"Subscription '{pcode}' must have a non-empty location_code.")
        if not rcode:
            raise ConfigError(f"Subscription '{pcode}' must have a non-empty rate_code.")
        key = (pcode, lcode, rcode)
        if key in seen_tuples:
            raise ConfigError(
                f"Duplicate subscription (provider_code, location_code, rate_code): {key!r}."
            )
        seen_tuples.add(key)

        for m in s.get("mappings") or []:
            cgcode = str(m.get("client_group_code", "")).strip()
            if not cgcode:
                raise ConfigError(
                    f"Subscription '{pcode}/{lcode}/{rcode}': "
                    "each mapping must have a non-empty client_group_code."
                )
            if cgcode not in group_codes:
                raise ConfigError(
                    f"Subscription '{pcode}/{lcode}/{rcode}': "
                    f"client_group_code '{cgcode}' is not listed in vehicle_groups."
                )
            ext_codes = m.get("external_codes") or []
            if not ext_codes:
                raise ConfigError(
                    f"Subscription '{pcode}/{lcode}/{rcode}' mapping for '{cgcode}' "
                    "must have at least one external_code."
                )


def _parse(raw: dict) -> OnboardingConfig:
    t = raw["tenant"]
    tenant = TenantConfig(
        name=t["name"].strip(),
        currency=t["currency"].strip().upper(),
        owner_email=t["owner_email"].strip(),
    )

    vehicle_groups = [
        VehicleGroupConfig(
            code=grp["code"].strip(),
            name=grp["name"].strip(),
            description=(grp.get("description") or "").strip() or None,
            display_order=int(grp["display_order"]),
        )
        for grp in raw.get("vehicle_groups") or []
    ]

    subscriptions = []
    for s in raw.get("subscriptions") or []:
        mappings = [
            MappingEntry(
                client_group_code=m["client_group_code"].strip(),
                external_codes=[str(c).strip() for c in m["external_codes"]],
            )
            for m in s.get("mappings") or []
        ]
        subscriptions.append(SubscriptionConfig(
            provider_code=s["provider_code"].strip(),
            location_code=s["location_code"].strip(),
            rate_code=s["rate_code"].strip(),
            mappings=mappings,
        ))

    return OnboardingConfig(tenant=tenant, vehicle_groups=vehicle_groups, subscriptions=subscriptions)
