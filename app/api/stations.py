"""REST-Endpunkte für Ladestationen (physische Modbus-TCP-Verbindungen).

Eine Ladestation ist nur die Verbindung + Profil; die steuerbaren
Ladepunkte verwaltet ``app.api.charge_points``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.models import ChargingStation, DeviceProfile
from app.modbus.client import ModbusError, StationClient
from app.modbus.runtime import StationSpec
from app.schemas import (
    ChargingStationCreate,
    ChargingStationRead,
    ChargingStationUpdate,
    ConnectionTestResult,
)

router = APIRouter(prefix="/api/stations", tags=["Ladestationen"])


def _require_profile(session: Session, profile_id: int) -> DeviceProfile:
    profile = session.get(DeviceProfile, profile_id)
    if profile is None:
        raise HTTPException(400, f"Geräteprofil {profile_id} existiert nicht")
    return profile


@router.get("", response_model=list[ChargingStationRead])
def list_stations(session: Session = Depends(get_session)):
    return session.query(ChargingStation).order_by(ChargingStation.name).all()


@router.get("/{station_id}", response_model=ChargingStationRead)
def get_station(station_id: int, session: Session = Depends(get_session)):
    station = session.get(ChargingStation, station_id)
    if station is None:
        raise HTTPException(404, "Ladestation nicht gefunden")
    return station


@router.post("", response_model=ChargingStationRead, status_code=201)
def create_station(data: ChargingStationCreate, session: Session = Depends(get_session)):
    _require_profile(session, data.profile_id)
    station = ChargingStation(**data.model_dump())
    session.add(station)
    session.commit()
    session.refresh(station)
    return station


@router.put("/{station_id}", response_model=ChargingStationRead)
def update_station(station_id: int, data: ChargingStationUpdate, session: Session = Depends(get_session)):
    station = session.get(ChargingStation, station_id)
    if station is None:
        raise HTTPException(404, "Ladestation nicht gefunden")
    _require_profile(session, data.profile_id)
    for key, value in data.model_dump().items():
        setattr(station, key, value)
    session.commit()
    session.refresh(station)
    return station


@router.delete("/{station_id}", status_code=204)
def delete_station(station_id: int, session: Session = Depends(get_session)):
    station = session.get(ChargingStation, station_id)
    if station is None:
        raise HTTPException(404, "Ladestation nicht gefunden")
    session.delete(station)  # löscht per cascade auch ihre Ladepunkte
    session.commit()
    return None


@router.post("/{station_id}/test", response_model=ConnectionTestResult)
async def test_station(station_id: int, session: Session = Depends(get_session)):
    """Verbindungs-/Profiltest: liest alle read-Register einmalig aus (roh,
    d. h. inkl. Connector-Suffix bei Doppel-Wallboxen)."""
    station = session.get(ChargingStation, station_id)
    if station is None:
        raise HTTPException(404, "Ladestation nicht gefunden")
    spec = StationSpec.from_station(station)
    client = StationClient(spec, timeout_s=settings.modbus_timeout_s, retries=settings.modbus_retries)
    try:
        values = await client.read_all()
        online = bool(values)
        error = None if online else "Keine Register lesbar"
    except (ModbusError, OSError) as exc:
        values, online, error = {}, False, str(exc)
    finally:
        await client.close()
    return ConnectionTestResult(station_id=station_id, name=station.name, online=online, values=values, error=error)
