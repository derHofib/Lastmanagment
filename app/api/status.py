"""REST-Endpunkt für den Systemzustand (Dashboard-Datenquelle)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.loadmanager.engine import PHASES
from app.loadmanager.loop import service
from app.models import ChargingStation, GlobalConfig
from app.schemas import StationLive, SystemStatus

router = APIRouter(prefix="/api", tags=["Status"])


@router.get("/status", response_model=SystemStatus)
def system_status(session: Session = Depends(get_session)):
    """Gesamtlast pro Phase, verfügbare Reserve und aktive Ladepunkte."""
    cfg = GlobalConfig.get_or_create(session)
    total = session.query(ChargingStation).count()
    snap = service.snapshot

    stations = [StationLive(**s) for s in snap.get("stations", {}).values()]
    return SystemStatus(
        management_mode=cfg.management_mode,
        distribution_strategy=cfg.distribution_strategy,
        grid_limit_current_a=cfg.grid_limit_current_a,
        effective_limit_current_a=snap.get("effective_limit_current_a", cfg.grid_limit_current_a),
        en14a_active=snap.get("en14a_active", False),
        phase_load_a=snap.get("phase_load_a", {p: 0.0 for p in PHASES}),
        phase_available_a=snap.get("phase_available_a", {p: 0.0 for p in PHASES}),
        active_stations=snap.get("active_stations", 0),
        total_stations=total,
        last_cycle=snap.get("last_cycle"),
        stations=stations,
    )
