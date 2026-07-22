"""Ladepunkt: die eigentlich steuerbare/zuteilbare Einheit einer Ladestation.

Eine Ladestation hat in der Regel genau einen Ladepunkt; Doppel-Wallboxen
(zwei unabhängig steuerbare Anschlüsse an einer Modbus-Verbindung) haben
zwei. ``connector_suffix`` legt fest, welche Register im Geräteprofil zu
diesem Ladepunkt gehören (Konvention: Register-Keys wie ``set_current`` /
``set_current_1`` / ``set_current_2`` – siehe app.modbus.runtime).
"""

from __future__ import annotations

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PhaseConfig, SafeState


class ChargePoint(Base):
    """Ein steuerbarer Ladepunkt (Anschluss) einer Ladestation."""

    __tablename__ = "charge_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("charging_stations.id", ondelete="CASCADE"), nullable=False
    )

    # "" für Einzel-Ladepunkt-Stationen, "_1"/"_2" ... für Doppel-Wallboxen.
    # Bestimmt, welche Register im Profil zu diesem Ladepunkt gehören
    # (z. B. "set_current" + connector_suffix).
    connector_suffix: Mapped[str] = mapped_column(String(10), default="", nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # Phasenanschluss bestimmt, welche Phasen der Ladepunkt belastet
    phase_config: Mapped[PhaseConfig] = mapped_column(
        Enum(PhaseConfig), default=PhaseConfig.P3, nullable=False
    )

    # Priorität für die priorisierte Verteilung (höher = wichtiger)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Technische Grenzen
    max_current_a: Mapped[float] = mapped_column(Float, default=32.0, nullable=False)
    min_current_a: Mapped[float] = mapped_column(Float, default=6.0, nullable=False)

    # An welchem Verteiler hängt der Abgang zu diesem Ladepunkt? None = Wurzel
    # (Hauptverteilung) – siehe app.models.distribution_board.
    distribution_board_id: Mapped[int | None] = mapped_column(
        ForeignKey("distribution_boards.id"), nullable=True
    )
    # Absicherung DES ABGANGS zu diesem Ladepunkt (Installationssicherung des
    # Kabels), separat von max_current_a (technische Grenze der Wallbox
    # selbst). None = keine gesonderte Abgangssicherung hinterlegt.
    circuit_breaker_a: Mapped[float | None] = mapped_column(Float, nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Verhalten im Fehlerfall (Kommunikationsverlust)
    safe_state: Mapped[SafeState] = mapped_column(
        Enum(SafeState), default=SafeState.BLOCK, nullable=False
    )

    # PV-Überschussladen: dieser Ladepunkt lädt ausschließlich mit
    # überschüssiger PV-Erzeugung (nachrangig, siehe allocate_tree-Zwei-Lauf
    # in app.loadmanager.engine). Erfordert dynamisches Lastmanagement mit
    # konfiguriertem Netzanschlusszähler; ohne verfügbaren Überschuss bleibt
    # der Ladepunkt konservativ auf 0 A (kein automatischer Rückfall auf
    # normale Netzladung).
    pv_surplus_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    station: Mapped["ChargingStation"] = relationship(  # noqa: F821
        "ChargingStation", back_populates="charge_points"
    )
    schedules: Mapped[list["ChargeSchedule"]] = relationship(  # noqa: F821
        "ChargeSchedule", back_populates="charge_point", cascade="all, delete-orphan"
    )

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
