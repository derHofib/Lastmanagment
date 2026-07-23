"""ORM-Modelle des Cloud-Diensts.

Bewusst schlank: keine Zeitreihen-Historie (kein Snapshot-Log), nur der
jeweils letzte empfangene Status je Installation wird gehalten
(Datensparsamkeit – siehe README-cloud.md).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cloud.db import Base


class CloudUser(Base):
    __tablename__ = "cloud_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    installations: Mapped[list["Installation"]] = relationship(
        "Installation", back_populates="owner", cascade="all, delete-orphan"
    )


class Installation(Base):
    """Eine gepaarte Voltibus-Installation (i. d. R. ein Raspberry Pi)."""

    __tablename__ = "installations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("cloud_users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # Gehashtes API-Token (wie ein Passwort behandelt); der Klartext wird nur
    # bei Erzeugung/Rotation einmalig zurückgegeben, nie wieder gespeichert.
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Letzter empfangener Status (JSON-Text, Struktur wie app.schemas.SystemStatus)
    last_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    owner: Mapped[CloudUser] = relationship("CloudUser", back_populates="installations")
