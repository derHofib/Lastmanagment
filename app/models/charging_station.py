"""Physische Ladestation (Modbus-TCP-Verbindung + Geräteprofil).

Eine Ladestation ist rein die physische Verbindung (IP/Port/Unit-ID) samt
zugeordnetem Geräteprofil. Die eigentlich steuerbaren/zuteilbaren Einheiten
sind ihre :class:`~app.models.charge_point.ChargePoint`-Ladepunkte (in der
Regel einer, bei Doppel-Wallboxen zwei) – siehe app.models.charge_point.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.device_profile import DeviceProfile


class ChargingStation(Base):
    """Physische Ladestation: Modbus-TCP-Verbindung + Geräteprofil."""

    __tablename__ = "charging_stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Modbus-TCP-Verbindung
    ip_address: Mapped[str] = mapped_column(String(60), nullable=False)
    tcp_port: Mapped[int] = mapped_column(Integer, default=502, nullable=False)
    unit_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("device_profiles.id"), nullable=False
    )

    profile: Mapped[DeviceProfile] = relationship("DeviceProfile")
    charge_points: Mapped[list["ChargePoint"]] = relationship(  # noqa: F821
        "ChargePoint", back_populates="station", cascade="all, delete-orphan",
        order_by="ChargePoint.connector_suffix",
    )
