"""REST-API-Router des Lastmanagements."""

from app.api.config import router as config_router
from app.api.profiles import router as profiles_router
from app.api.stations import router as stations_router
from app.api.status import router as status_router

__all__ = ["config_router", "profiles_router", "stations_router", "status_router"]
