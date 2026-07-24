"""Fail-Safe-Logik: sichere Zustände und konservative Kapazitätsreservierung.

Grundsätze (nicht verhandelbar):
    * Die Summenbegrenzung je Phase ist eine harte Obergrenze.
    * Bei Unsicherheit (z. B. Kommunikationsverlust) wird konservativ – also
      zugunsten von weniger Strom – entschieden.
"""

from __future__ import annotations

from app.loadmanager.engine import PHASES
from app.models.base import SafeState
from app.modbus.runtime import ChargePointSpec


def safe_setpoint(charge_point: ChargePointSpec) -> float:
    """Sollstrom im sicheren Zustand eines Ladepunkts."""
    if charge_point.safe_state == SafeState.MIN_CURRENT.value:
        return charge_point.min_current_a
    return 0.0  # BLOCK


def reserved_current(charge_point: ChargePointSpec) -> float:
    """Strom, den ein nicht erreichbarer Ladepunkt im sicheren Zustand zieht.

    Diese Reserve wird von der verfügbaren Kapazität abgezogen, damit die
    steuerbaren Ladepunkte die harte Grenze auch dann nicht sprengen, wenn
    ein unerreichbarer Ladepunkt weiter (mit Minimalstrom) lädt.
    """
    return safe_setpoint(charge_point)


def subtract_reservations(
    capacity: dict[str, float],
    offline_charge_points: list[ChargePointSpec],
) -> dict[str, float]:
    """Zieht die Reserven unerreichbarer Ladepunkte von der Kapazität ab."""
    result = dict(capacity)
    for cp in offline_charge_points:
        reserve = reserved_current(cp)
        for phase in cp.phases:
            if phase in result:
                result[phase] = max(0.0, result[phase] - reserve)
    return result


def clamp_capacity(capacity: dict[str, float]) -> dict[str, float]:
    """Stellt sicher, dass keine Phase eine negative Kapazität aufweist."""
    return {p: max(0.0, capacity.get(p, 0.0)) for p in PHASES}
