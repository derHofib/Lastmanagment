"""REST-Endpunkte für Ladepunkte (die steuerbare/zuteilbare Einheit).

Eine Ladestation hat in der Regel einen Ladepunkt, Doppel-Wallboxen zwei
(unterschieden durch ``connector_suffix``). Priorität, Phasen, Min/Max-
Strom, Verteiler-Zuordnung, Fail-Safe, PV-Überschussladen und Zeitpläne
werden hier verwaltet – siehe app.models.charge_point.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.loadmanager.loop import service
from app.models import ChargePoint, ChargeSchedule, ChargingStation, DistributionBoard
from app.schemas import (
    ChargePointCreate,
    ChargePointLive,
    ChargePointRead,
    ChargePointUpdate,
    ChargeScheduleCreate,
)

router = APIRouter(prefix="/api/charge-points", tags=["Ladepunkte"])


def _require_station(session: Session, station_id: int) -> None:
    if session.get(ChargingStation, station_id) is None:
        raise HTTPException(400, f"Ladestation {station_id} existiert nicht")


def _require_board(session: Session, board_id: int | None) -> None:
    if board_id is not None and session.get(DistributionBoard, board_id) is None:
        raise HTTPException(400, f"Verteiler {board_id} existiert nicht")


def _apply_schedules(cp: ChargePoint, schedules: list[ChargeScheduleCreate]) -> None:
    """Ersetzt die Zeitpläne eines Ladepunkts vollständig."""
    cp.schedules.clear()
    for sch in schedules:
        cp.schedules.append(
            ChargeSchedule(
                weekdays_mask=sch.weekdays_mask,
                start_time=sch.start_time,
                end_time=sch.end_time,
            )
        )


@router.get("", response_model=list[ChargePointRead])
def list_charge_points(session: Session = Depends(get_session)):
    return session.query(ChargePoint).order_by(ChargePoint.name).all()


@router.get("/{cp_id}", response_model=ChargePointRead)
def get_charge_point(cp_id: int, session: Session = Depends(get_session)):
    cp = session.get(ChargePoint, cp_id)
    if cp is None:
        raise HTTPException(404, "Ladepunkt nicht gefunden")
    return cp


@router.post("", response_model=ChargePointRead, status_code=201)
def create_charge_point(data: ChargePointCreate, session: Session = Depends(get_session)):
    _require_station(session, data.station_id)
    _require_board(session, data.distribution_board_id)
    payload = data.model_dump(exclude={"schedules"})
    cp = ChargePoint(**payload)
    _apply_schedules(cp, data.schedules)
    session.add(cp)
    session.commit()
    session.refresh(cp)
    return cp


@router.put("/{cp_id}", response_model=ChargePointRead)
def update_charge_point(cp_id: int, data: ChargePointUpdate, session: Session = Depends(get_session)):
    cp = session.get(ChargePoint, cp_id)
    if cp is None:
        raise HTTPException(404, "Ladepunkt nicht gefunden")
    _require_station(session, data.station_id)
    _require_board(session, data.distribution_board_id)
    for key, value in data.model_dump(exclude={"schedules"}).items():
        setattr(cp, key, value)
    if data.schedules is not None:
        _apply_schedules(cp, data.schedules)
    session.commit()
    session.refresh(cp)
    return cp


@router.delete("/{cp_id}", status_code=204)
def delete_charge_point(cp_id: int, session: Session = Depends(get_session)):
    cp = session.get(ChargePoint, cp_id)
    if cp is None:
        raise HTTPException(404, "Ladepunkt nicht gefunden")
    session.delete(cp)
    session.commit()
    return None


@router.get("/{cp_id}/live", response_model=ChargePointLive)
def charge_point_live(cp_id: int, session: Session = Depends(get_session)):
    """Aktuelle Messwerte aus der letzten Momentaufnahme des Regelzyklus."""
    cp = session.get(ChargePoint, cp_id)
    if cp is None:
        raise HTTPException(404, "Ladepunkt nicht gefunden")
    snap = service.snapshot.get("charge_points", {}).get(cp_id)
    if snap is None:
        return ChargePointLive(charge_point_id=cp_id, name=cp.name, online=False)
    return ChargePointLive(**snap)
