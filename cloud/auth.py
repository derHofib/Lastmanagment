"""Authentifizierung des Cloud-Diensts.

Zwei getrennte Credential-Arten:
- **Browser-Session** (Mensch, Dashboard): Passwort (bcrypt) + JWT in einem
  httpOnly-Cookie. Gleiche Origin wie die API (siehe cloud/main.py) – kein
  CORS nötig.
- **Installations-Token** (Maschine, ``POST /api/ingest``): zufälliges Token,
  nur gehasht gespeichert, im Klartext ausschließlich bei Erzeugung/Rotation
  einmalig zurückgegeben (Standard-API-Key-UX).
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from cloud.config import settings
from cloud.db import get_session
from cloud.models import CloudUser, Installation

SESSION_COOKIE = "voltibus_cloud_session"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def create_session_token(user_id: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_session_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def get_current_user(
    session: Session = Depends(get_session),
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> CloudUser:
    user_id = decode_session_token(session_cookie) if session_cookie else None
    if user_id is None:
        raise HTTPException(401, "Nicht angemeldet")
    user = session.get(CloudUser, user_id)
    if user is None:
        raise HTTPException(401, "Nicht angemeldet")
    return user


# --- Installations-Token (Maschine-zu-Maschine, /api/ingest) ---------------

def generate_installation_token() -> str:
    return "vltb_" + secrets.token_urlsafe(32)


def hash_installation_token(token: str) -> str:
    # SHA-256 statt bcrypt: hohe Entropie (kein Wörterbuch-Angriff möglich),
    # dafür schnell zu prüfen – jeder Ingest-Aufruf einer Installation prüft
    # dieses Token, ein bcrypt-Vergleich wäre unnötig teuer für diesen Zweck.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_installation_by_token(session: Session, request: Request) -> Installation:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(401, "Fehlendes Bearer-Token")
    token = auth_header[7:].strip()
    token_hash = hash_installation_token(token)
    installation = session.query(Installation).filter_by(token_hash=token_hash).first()
    if installation is None:
        raise HTTPException(401, "Ungültiges Installations-Token")
    return installation


# --- Einfacher In-Memory-Rate-Limiter für /api/auth/login -------------------
# Nur pro Prozess (kein gemeinsamer Speicher über mehrere Instanzen hinweg) –
# für einen Mehrinstanz-Betrieb wäre ein gemeinsamer Store (z. B. Redis)
# nötig, siehe README-cloud.md.

_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_WINDOW_S = 60.0
_MAX_ATTEMPTS = 10


def check_login_rate_limit(client_ip: str) -> None:
    now = time.monotonic()
    attempts = [t for t in _LOGIN_ATTEMPTS.get(client_ip, []) if now - t < _WINDOW_S]
    if len(attempts) >= _MAX_ATTEMPTS:
        raise HTTPException(429, "Zu viele Anmeldeversuche. Bitte kurz warten.")
    attempts.append(now)
    _LOGIN_ATTEMPTS[client_ip] = attempts
