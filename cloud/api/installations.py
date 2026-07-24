"""CRUD für Installationen (gepaarte Voltibus-Instanzen) + Token-Verwaltung."""

from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from cloud.auth import generate_installation_token, get_current_user, hash_installation_token
from cloud.db import get_session
from cloud.models import CloudUser, Installation
from cloud.schemas import InstallationCreate, InstallationCreated, InstallationRead
from cloud.timeutil import utcnow

router = APIRouter(prefix="/api/installations", tags=["Installationen"])

# Als "online" gilt eine Installation, wenn sie innerhalb dieses Fensters
# zuletzt einen Status gesendet hat. Größer als das Standard-Relay-Intervall
# (30s, siehe app.models.GlobalConfig.cloud_relay_interval_s), damit ein
# einzelner verpasster Zyklus nicht sofort als "offline" erscheint.
ONLINE_THRESHOLD_S = 90


def _to_read(inst: Installation) -> InstallationRead:
    online = (
        inst.last_seen_at is not None
        and utcnow() - inst.last_seen_at < timedelta(seconds=ONLINE_THRESHOLD_S)
    )
    snapshot = json.loads(inst.last_snapshot_json) if inst.last_snapshot_json else None
    return InstallationRead(
        id=inst.id, name=inst.name, created_at=inst.created_at,
        last_seen_at=inst.last_seen_at, online=online,
        last_snapshot=snapshot, last_snapshot_at=inst.last_snapshot_at,
    )


def _require_owned(session: Session, user: CloudUser, installation_id: int) -> Installation:
    inst = session.get(Installation, installation_id)
    if inst is None or inst.owner_id != user.id:
        raise HTTPException(404, "Installation nicht gefunden")
    return inst


@router.get("", response_model=list[InstallationRead])
def list_installations(
    user: CloudUser = Depends(get_current_user), session: Session = Depends(get_session)
):
    installations = session.query(Installation).filter_by(owner_id=user.id).order_by(Installation.name).all()
    return [_to_read(i) for i in installations]


@router.post("", response_model=InstallationCreated, status_code=201)
def create_installation(
    data: InstallationCreate,
    user: CloudUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    token = generate_installation_token()
    inst = Installation(
        owner_id=user.id, name=data.name,
        token_hash=hash_installation_token(token),
        created_at=utcnow(),
    )
    session.add(inst)
    session.commit()
    session.refresh(inst)
    return InstallationCreated(**_to_read(inst).model_dump(), token=token)


@router.post("/{installation_id}/rotate-token", response_model=InstallationCreated)
def rotate_token(
    installation_id: int,
    user: CloudUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    inst = _require_owned(session, user, installation_id)
    token = generate_installation_token()
    inst.token_hash = hash_installation_token(token)
    session.commit()
    session.refresh(inst)
    return InstallationCreated(**_to_read(inst).model_dump(), token=token)


@router.delete("/{installation_id}", status_code=204)
def delete_installation(
    installation_id: int,
    user: CloudUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    inst = _require_owned(session, user, installation_id)
    session.delete(inst)
    session.commit()
    return None
