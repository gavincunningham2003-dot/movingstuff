"""Multi-hazard exposure engine (one module per hazard, common output contract)."""

from catrix.hazards.base import HazardInfo, HazardModule, HazardResult
from catrix.hazards.registry import assess_all_addresses, assess_points, build_modules, catalogue

__all__ = [
    "HazardInfo",
    "HazardModule",
    "HazardResult",
    "assess_all_addresses",
    "assess_points",
    "build_modules",
    "catalogue",
]
