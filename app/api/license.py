"""REST-Endpunkte für den Lizenzstand.

Die Anzahl der Ladestationen wird bewusst gegen ``ChargingStation`` gezählt
(die physische Modbus-Verbindung), nicht gegen ``ChargePoint`` – deckt sich
mit der wörtlichen Formulierung "N Ladestationen" der Lizenzstufen.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.licensing import effective_max_stations, verify_license_key
from app.models import ChargingStation, License
from app.schemas import LicenseActivate, LicenseRead

router = APIRouter(prefix="/api/license", tags=["Lizenz"])


def _to_read(session: Session, lic: License) -> LicenseRead:
    used = session.query(ChargingStation).count()
    return LicenseRead(
        tier=lic.tier,
        max_stations=effective_max_stations(lic.tier, lic.max_stations),
        used_stations=used,
        issued_to=lic.issued_to,
        activated_at=lic.activated_at,
    )


@router.get("", response_model=LicenseRead)
def get_license(session: Session = Depends(get_session)):
    lic = License.get_or_create(session)
    return _to_read(session, lic)


@router.post("/activate", response_model=LicenseRead)
def activate_license(data: LicenseActivate, session: Session = Depends(get_session)):
    try:
        payload = verify_license_key(data.key)
    except ValueError as exc:
        raise HTTPException(400, f"Ungültiger Lizenzschlüssel: {exc}") from exc

    lic = License.get_or_create(session)
    lic.tier = payload.tier
    lic.raw_key = data.key
    lic.max_stations = payload.max_stations
    lic.issued_to = payload.issued_to
    lic.activated_at = datetime.now(UTC)
    session.commit()
    session.refresh(lic)
    return _to_read(session, lic)
