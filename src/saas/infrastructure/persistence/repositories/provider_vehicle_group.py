# Backward-compatibility shim.  Import from the canonical module instead.
# Removed in prompt 4 when the scraper is refactored.
from .provider_vehicle_category_repository import ProviderVehicleCategoryRepository as ProviderVehicleGroupRepository  # noqa: F401

__all__ = ["ProviderVehicleGroupRepository"]
