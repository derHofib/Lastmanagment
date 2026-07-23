"""REST-API-Router des Lastmanagements."""

from app.api.boards import router as boards_router
from app.api.charge_points import router as charge_points_router
from app.api.config import router as config_router
from app.api.license import router as license_router
from app.api.profiles import router as profiles_router
from app.api.stations import router as stations_router
from app.api.status import router as status_router

__all__ = [
    "boards_router",
    "charge_points_router",
    "config_router",
    "license_router",
    "profiles_router",
    "stations_router",
    "status_router",
]
