"""Messwert-Historie (optional persistiert)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Measurement(Base):
    """Ein einzelner gemessener Wert eines Ladepunkts zu einem Zeitpunkt."""

    __tablename__ = "measurements"
    __table_args__ = (
        Index("ix_measurement_charge_point_ts", "charge_point_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    charge_point_id: Mapped[int] = mapped_column(
        ForeignKey("charge_points.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
