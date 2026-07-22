"""REST-Endpunkte für Ladestationen inkl. Live-Werten und Verbindungstest."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.loadmanager.loop import service
from app.models import ChargingStation, DeviceProfile, DistributionBoard
from app.modbus.client import ModbusError, StationClient
from app.modbus.runtime import StationSpec
from app.schemas import (
    ChargingStationCreate,
    ChargingStationRead,
    ChargingStationUpdate,
    StationLive,
)

router = APIRouter(prefix="/api/stations", tags=["Ladestationen"])


def _require_profile(session: Session, profile_id: int) -> DeviceProfile:
    profile = session.get(DeviceProfile, profile_id)
    if profile is None:
        raise HTTPException(400, f"Geräteprofil {profile_id} existiert nicht")
    return profile


def _require_board(session: Session, board_id: int | None) -> None:
    if board_id is not None and session.get(DistributionBoard, board_id) is None:
        raise HTTPException(400, f"Verteiler {board_id} existiert nicht")


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
    _require_board(session, data.distribution_board_id)
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
    _require_board(session, data.distribution_board_id)
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
    session.delete(station)
    session.commit()
    return None


@router.get("/{station_id}/live", response_model=StationLive)
def station_live(station_id: int, session: Session = Depends(get_session)):
    """Aktuelle Messwerte aus der letzten Momentaufnahme des Regelzyklus."""
    station = session.get(ChargingStation, station_id)
    if station is None:
        raise HTTPException(404, "Ladestation nicht gefunden")
    snap = service.snapshot.get("stations", {}).get(station_id)
    if snap is None:
        return StationLive(station_id=station_id, name=station.name, online=False)
    return StationLive(**snap)


@router.post("/{station_id}/test", response_model=StationLive)
async def test_station(station_id: int, session: Session = Depends(get_session)):
    """Verbindungs-/Profiltest: liest alle read-Register einmalig aus."""
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
    return StationLive(station_id=station_id, name=station.name, online=online, values=values, error=error)
