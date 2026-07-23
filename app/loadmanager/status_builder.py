"""Baut den Systemzustand (app.schemas.SystemStatus) aus DB + Regelzyklus-Snapshot.

Eigenständiges Modul (statt Teil von ``app.api.status``), damit
``app.loadmanager.cloud_relay`` es verwenden kann, ohne das ``app.api``-Paket
zu importieren – das würde sonst einen Zirkelimport erzeugen
(``app.api.config`` importiert ``cloud_relay``, welches wiederum
``app.api.status`` importieren würde).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.loadmanager.engine import PHASES
from app.loadmanager.loop import service
from app.models import ChargePoint, GlobalConfig
from app.schemas import ChargePointLive, SystemStatus


def build_system_status(session: Session) -> SystemStatus:
    """Baut den Systemzustand (Gesamtlast pro Phase, Reserve, Ladepunkte)."""
    cfg = GlobalConfig.get_or_create(session)
    total = session.query(ChargePoint).count()
    snap = service.snapshot

    charge_points = [ChargePointLive(**cp) for cp in snap.get("charge_points", {}).values()]
    return SystemStatus(
        management_mode=cfg.management_mode,
        distribution_strategy=cfg.distribution_strategy,
        grid_limit_current_a=cfg.grid_limit_current_a,
        effective_limit_current_a=snap.get("effective_limit_current_a", cfg.grid_limit_current_a),
        en14a_active=snap.get("en14a_active", False),
        phase_load_a=snap.get("phase_load_a", {p: 0.0 for p in PHASES}),
        phase_available_a=snap.get("phase_available_a", {p: 0.0 for p in PHASES}),
        active_charge_points=snap.get("active_stations", 0),
        total_charge_points=total,
        last_cycle=snap.get("last_cycle"),
        charge_points=charge_points,
    )
