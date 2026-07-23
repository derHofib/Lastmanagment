"""REST-Endpunkte für die globale Konfiguration."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.loadmanager.cloud_relay import service as cloud_relay_service
from app.models import DeviceProfile, GlobalConfig
from app.schemas import GlobalConfigRead, GlobalConfigUpdate

router = APIRouter(prefix="/api/config", tags=["Konfiguration"])


@router.get("", response_model=GlobalConfigRead)
def get_config(session: Session = Depends(get_session)):
    return GlobalConfig.get_or_create(session)


@router.put("", response_model=GlobalConfigRead)
def update_config(data: GlobalConfigUpdate, session: Session = Depends(get_session)):
    cfg = GlobalConfig.get_or_create(session)
    updates = data.model_dump(exclude_unset=True)
    if updates.get("meter_profile_id"):
        if session.get(DeviceProfile, updates["meter_profile_id"]) is None:
            raise HTTPException(400, "Zähler-Geräteprofil existiert nicht")
    for key, value in updates.items():
        setattr(cfg, key, value)
    session.commit()
    session.refresh(cfg)
    return cfg


@router.post("/cloud-relay/test")
async def test_cloud_relay():
    """Sendet einmalig den aktuellen Status an die konfigurierte Cloud-URL
    (unabhängig davon, ob die Anbindung bereits aktiviert ist) – für den
    „Verbindung jetzt testen"-Button in den Einstellungen."""
    return await cloud_relay_service.test_now()
