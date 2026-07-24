"""Wiederkehrende Sperrzeiten (Zeitpläne) für Ladepunkte.

Manuelles Ein-/Ausschalten gibt es bereits über ``ChargePoint.enabled``; ein
``ChargeSchedule`` ergänzt das um automatische, wiederkehrende Sperrfenster
(z. B. "täglich 22:00–06:00 aus"). Die Auswertung (liegt "jetzt" in einem
Fenster?) übernimmt die reine Funktion in app.loadmanager.schedule.
"""

from __future__ import annotations

from datetime import time as dt_time

from sqlalchemy import ForeignKey, Integer, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ChargeSchedule(Base):
    """Ein wiederkehrendes Sperrfenster für einen Ladepunkt."""

    __tablename__ = "charge_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    charge_point_id: Mapped[int] = mapped_column(
        ForeignKey("charge_points.id", ondelete="CASCADE"), nullable=False
    )

    # Bitmaske: Bit 0 = Montag ... Bit 6 = Sonntag (1 = Fenster gilt an diesem
    # Tag). Bezieht sich auf den Tag, an dem das Fenster BEGINNT.
    weekdays_mask: Mapped[int] = mapped_column(Integer, default=0b1111111, nullable=False)

    start_time: Mapped[dt_time] = mapped_column(Time, nullable=False)
    end_time: Mapped[dt_time] = mapped_column(Time, nullable=False)

    charge_point: Mapped["ChargePoint"] = relationship(  # noqa: F821
        "ChargePoint", back_populates="schedules"
    )
