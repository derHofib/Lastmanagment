"""Empfängt periodische Status-Updates lokaler Voltibus-Installationen."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from cloud.auth import get_installation_by_token
from cloud.db import get_session
from cloud.timeutil import utcnow

router = APIRouter(prefix="/api/ingest", tags=["Ingest"])


@router.post("", status_code=204)
async def ingest(request: Request, session: Session = Depends(get_session)):
    """Nimmt den Status einer Installation entgegen (Bearer-Token-Auth).

    Der Body ist bewusst nicht als striktes Pydantic-Schema typisiert: er
    entspricht der Struktur von ``GET /api/status`` der lokalen Installation
    (``app.schemas.SystemStatus``), die sich unabhängig vom Cloud-Dienst
    weiterentwickeln kann – der Cloud-Dienst speichert ihn nur unverändert.
    """
    installation = get_installation_by_token(session, request)
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "Ungültiger JSON-Body") from exc

    now = utcnow()
    installation.last_snapshot_json = json.dumps(payload)
    installation.last_snapshot_at = now
    installation.last_seen_at = now
    session.commit()
    return None
