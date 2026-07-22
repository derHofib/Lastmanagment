"""Fail-Safe-Logik: sichere Zustände und konservative Kapazitätsreservierung.

Grundsätze (nicht verhandelbar):
    * Die Summenbegrenzung je Phase ist eine harte Obergrenze.
    * Bei Unsicherheit (z. B. Kommunikationsverlust) wird konservativ – also
      zugunsten von weniger Strom – entschieden.
"""

from __future__ import annotations

from app.loadmanager.engine import PHASES
from app.models.base import SafeState
from app.modbus.runtime import StationSpec


def safe_setpoint(station: StationSpec) -> float:
    """Sollstrom im sicheren Zustand einer Station."""
    if station.safe_state == SafeState.MIN_CURRENT.value:
        return station.min_current_a
    return 0.0  # BLOCK


def reserved_current(station: StationSpec) -> float:
    """Strom, den eine nicht erreichbare Station im sicheren Zustand zieht.

    Diese Reserve wird von der verfügbaren Kapazität abgezogen, damit die
    steuerbaren Stationen die harte Grenze auch dann nicht sprengen, wenn eine
    unerreichbare Box weiter (mit Minimalstrom) lädt.
    """
    return safe_setpoint(station)


def subtract_reservations(
    capacity: dict[str, float],
    offline_stations: list[StationSpec],
) -> dict[str, float]:
    """Zieht die Reserven unerreichbarer Stationen von der Kapazität ab."""
    result = dict(capacity)
    for st in offline_stations:
        reserve = reserved_current(st)
        for phase in st.phases:
            if phase in result:
                result[phase] = max(0.0, result[phase] - reserve)
    return result


def clamp_capacity(capacity: dict[str, float]) -> dict[str, float]:
    """Stellt sicher, dass keine Phase eine negative Kapazität aufweist."""
    return {p: max(0.0, capacity.get(p, 0.0)) for p in PHASES}
