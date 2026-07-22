"""Konkrete Ladestation (Instanz eines Geräteprofils)."""

from __future__ import annotations

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PhaseConfig, SafeState
from app.models.device_profile import DeviceProfile


class ChargingStation(Base):
    """Physische Ladestation mit Netzwerk- und Anschlussparametern."""

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

    # Phasenanschluss bestimmt, welche Phasen die Box belastet
    phase_config: Mapped[PhaseConfig] = mapped_column(
        Enum(PhaseConfig), default=PhaseConfig.P3, nullable=False
    )

    # Priorität für die priorisierte Verteilung (höher = wichtiger)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Technische Grenzen der Box
    max_current_a: Mapped[float] = mapped_column(Float, default=32.0, nullable=False)
    min_current_a: Mapped[float] = mapped_column(Float, default=6.0, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Verhalten im Fehlerfall (Kommunikationsverlust)
    safe_state: Mapped[SafeState] = mapped_column(
        Enum(SafeState), default=SafeState.BLOCK, nullable=False
    )

    profile: Mapped[DeviceProfile] = relationship("DeviceProfile")

    @property
    def phases(self) -> tuple[str, ...]:
        """Liste der belasteten Phasen, z. B. ``("L1",)`` oder ``("L1","L2","L3")``."""
        mapping = {
            PhaseConfig.P1_L1: ("L1",),
            PhaseConfig.P1_L2: ("L2",),
            PhaseConfig.P1_L3: ("L3",),
            PhaseConfig.P3: ("L1", "L2", "L3"),
        }
        return mapping[self.phase_config]
