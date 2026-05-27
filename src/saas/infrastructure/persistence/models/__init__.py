from .base import Base
from .catalog import (
    AcrissCode,
    Provider,
    ProviderLocation,
    ProviderRate,
    ProviderRecipe,
    ProviderVehicleCategory,
    ScrapeRun,
    HomogeneousZone,
    PriceObservation,
    PriceObservationHeartbeat,
)
from .tenant import (
    Tenant,
    User,
    TenantVehicleGroup,
    TenantVehicleGroupMapping,
    TenantSubscription,
    PricingRule,
    PricingOutput,
    # Backward-compatibility aliases (removed in prompt 4)
    ClientVehicleGroup,
    VehicleGroupMapping,
)

__all__ = [
    "Base",
    "AcrissCode",
    "Provider",
    "ProviderLocation",
    "ProviderRate",
    "ProviderVehicleCategory",
    "ScrapeRun",
    "HomogeneousZone",
    "PriceObservation",
    "PriceObservationHeartbeat",
    "ProviderRecipe",
    "Tenant",
    "User",
    "TenantVehicleGroup",
    "TenantVehicleGroupMapping",
    "TenantSubscription",
    "PricingRule",
    "PricingOutput",
    # Backward-compatibility aliases (removed in prompt 4)
    "ClientVehicleGroup",
    "VehicleGroupMapping",
]
