"""Registrierung/Login/Logout für das Cloud-Dashboard (Browser-Session)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from cloud.auth import (
    SESSION_COOKIE,
    check_login_rate_limit,
    create_session_token,
    get_current_user,
    hash_password,
    verify_password,
)
from cloud.config import settings
from cloud.db import get_session
from cloud.models import CloudUser
from cloud.schemas import LoginRequest, RegisterRequest, UserRead
from cloud.timeutil import utcnow

router = APIRouter(prefix="/api/auth", tags=["Auth"])

_COOKIE_MAX_AGE_S = int(60 * 60 * 24 * 14)  # 14 Tage, siehe CloudSettings.jwt_expiry_hours


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE, value=token, httponly=True, samesite="lax",
        secure=settings.cookie_secure, max_age=_COOKIE_MAX_AGE_S, path="/",
    )


@router.post("/register", response_model=UserRead, status_code=201)
def register(data: RegisterRequest, response: Response, session: Session = Depends(get_session)):
    if session.query(CloudUser).filter_by(email=data.email).first() is not None:
        raise HTTPException(409, "E-Mail-Adresse ist bereits registriert")
    user = CloudUser(
        email=data.email,
        password_hash=hash_password(data.password),
        created_at=utcnow(),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    _set_session_cookie(response, create_session_token(user.id))
    return user


@router.post("/login", response_model=UserRead)
def login(data: LoginRequest, request: Request, response: Response, session: Session = Depends(get_session)):
    check_login_rate_limit(request.client.host if request.client else "unknown")
    user = session.query(CloudUser).filter_by(email=data.email).first()
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "E-Mail oder Passwort ist falsch")
    _set_session_cookie(response, create_session_token(user.id))
    return user


@router.post("/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return None


@router.get("/me", response_model=UserRead)
def me(user: CloudUser = Depends(get_current_user)):
    return user
