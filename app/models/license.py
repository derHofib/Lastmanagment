"""Lizenzzustand (Singleton-Zeile, analog zu :class:`GlobalConfig`).

Die Prüfung des Lizenzschlüssels selbst (Signatur, Tier-Limits) sitzt in
``app.licensing`` – dieses Modell speichert nur das Ergebnis der letzten
Aktivierung.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models.base import Base, LicenseTier


class License(Base):
    """Aktueller Lizenzstand. Es existiert genau eine Zeile (id=1)."""

    __tablename__ = "license"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    tier: Mapped[LicenseTier] = mapped_column(
        Enum(LicenseTier), default=LicenseTier.FREE, nullable=False
    )
    # Der zuletzt aktivierte Schlüsseltext (zur Nachvollziehbarkeit/Re-Prüfung)
    raw_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Explizite Override aus dem Schlüssel-Payload; None = Tier-Standardlimit gilt
    max_stations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Optionales Label aus dem Schlüssel (z. B. Kundenname)
    issued_to: Mapped[str | None] = mapped_column(String(120), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @staticmethod
    def get_or_create(session: Session) -> "License":
        """Liefert den Lizenzstand, erzeugt bei Bedarf die Free-Standardzeile."""
        lic = session.get(License, 1)
        if lic is None:
            lic = License(id=1)
            session.add(lic)
            session.commit()
            session.refresh(lic)
        return lic
