"""Pydantic-Schemas des Cloud-Diensts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    created_at: datetime


class InstallationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class InstallationRead(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_seen_at: datetime | None
    online: bool
    last_snapshot: dict[str, Any] | None
    last_snapshot_at: datetime | None


class InstallationCreated(InstallationRead):
    # Klartext-Token: wird nur bei Erzeugung/Rotation einmalig zurückgegeben.
    token: str
