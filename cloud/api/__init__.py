"""REST-API-Router des Cloud-Diensts."""

from cloud.api.auth import router as auth_router
from cloud.api.ingest import router as ingest_router
from cloud.api.installations import router as installations_router

__all__ = ["auth_router", "ingest_router", "installations_router"]
