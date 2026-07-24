"""Verteilungshierarchie: Hauptverteilung, Unterverteilungen und ihre Absicherung.

Bildet die reale Elektroinstallation ab: eine Hauptverteilung (Hausanschluss)
mit eigener Absicherung, davon abgehende Unterverteilungen (jeweils mit
eigener Zuleitungs-Absicherung), und darunter die Abgänge zu den
Ladestationen (siehe ``ChargingStation.circuit_breaker_a``).

Annahme: Jeder Verteiler ist dreiphasig angeschlossen, die Absicherung gilt
gleichermaßen für L1/L2/L3 (wie ein dreipoliger Sicherungsautomat) – das
entspricht dem bestehenden Modell von ``GlobalConfig.grid_limit_current_a``,
nur pro Baumknoten wiederholt.
"""

from __future__ import annotations

from sqlalchemy import Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.models.base import Base, DistributionStrategy


class DistributionBoard(Base):
    """Verteiler (Hauptverteilung oder Unterverteilung) im Verteilungsbaum."""

    __tablename__ = "distribution_boards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # None = Wurzel der Hierarchie (Hauptverteilung). Es existiert immer
    # genau ein Wurzel-Verteiler, siehe get_or_create_root().
    parent_board_id: Mapped[int | None] = mapped_column(
        ForeignKey("distribution_boards.id"), nullable=True
    )

    # Absicherung der Zuleitung zu diesem Verteiler (z. B. Hausanschluss 63 A,
    # Unterverteilung 35 A) – gilt je Phase.
    incoming_fuse_a: Mapped[float] = mapped_column(Float, default=63.0, nullable=False)

    # Priorität zwischen Geschwister-Zweigen bei der priority-Strategie
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Verteilstrategie für DIESEN Verteiler-Zweig. None = erbt vom
    # übergeordneten Verteiler (kaskadierend) bzw. zuletzt von der globalen
    # Strategie (GlobalConfig.distribution_strategy) an der Wurzel.
    strategy: Mapped[DistributionStrategy | None] = mapped_column(
        Enum(DistributionStrategy), nullable=True
    )

    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Position im Baukasten-Topologie-Canvas (frei per Drag&Drop platziert).
    # None = noch nicht platziert, Frontend nutzt ein Fallback-Rasterlayout.
    canvas_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    canvas_y: Mapped[float | None] = mapped_column(Float, nullable=True)

    parent: Mapped["DistributionBoard | None"] = relationship(
        "DistributionBoard", remote_side=[id], back_populates="children"
    )
    children: Mapped[list["DistributionBoard"]] = relationship(
        "DistributionBoard", back_populates="parent"
    )

    @staticmethod
    def get_or_create_root(session: Session, default_fuse_a: float = 63.0) -> "DistributionBoard":
        """Liefert die Wurzel (Hauptverteilung), erzeugt sie bei Bedarf.

        Der Default für die Absicherung entspricht dem aktuellen
        ``grid_limit_current_a``, damit Bestandsinstallationen ohne
        Unterverteiler unverändert weiterlaufen.
        """
        root = session.query(DistributionBoard).filter(
            DistributionBoard.parent_board_id.is_(None)
        ).first()
        if root is None:
            root = DistributionBoard(name="Hauptverteilung", incoming_fuse_a=default_fuse_a)
            session.add(root)
            session.commit()
            session.refresh(root)
        return root
