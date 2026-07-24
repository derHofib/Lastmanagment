"""REST-Endpunkt für den Systemzustand (Dashboard-Datenquelle)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.loadmanager.status_builder import build_system_status
from app.schemas import SystemStatus

router = APIRouter(prefix="/api", tags=["Status"])


@router.get("/status", response_model=SystemStatus)
def system_status(session: Session = Depends(get_session)):
    """Gesamtlast pro Phase, verfügbare Reserve und aktive Ladepunkte."""
    return build_system_status(session)
