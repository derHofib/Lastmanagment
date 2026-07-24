"""Hysterese und Mindesthaltezeiten für Sollwerte.

Verhindert Relais-Flattern: kleine Sollwertänderungen werden geglättet und
ein frisch gesetzter Sollwert eine Mindestzeit gehalten, bevor er sich wieder
ändern darf. Erhöhungen bis zum Grenzschutz bleiben jederzeit möglich, wenn
sie eine Reduzierung darstellen (Sicherheit geht vor Komfort).
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class _SetpointState:
    value: float
    changed_at: float


class SetpointSmoother:
    """Verwaltet je Station den zuletzt geschriebenen Sollwert.

    :param min_change_a: Änderungen kleiner als dieser Betrag werden ignoriert.
    :param min_hold_s: Mindesthaltezeit, bevor ein Sollwert erneut steigen darf.
    """

    def __init__(self, min_change_a: float = 1.0, min_hold_s: float = 30.0,
                 time_fn=time.monotonic):
        self.min_change_a = min_change_a
        self.min_hold_s = min_hold_s
        self._time = time_fn
        self._state: dict[int, _SetpointState] = {}

    def reset(self, station_id: int) -> None:
        """Vergisst den Zustand einer Station (z. B. nach Fehler/Abstecken)."""
        self._state.pop(station_id, None)

    def desired(self, station_id: int, target: float) -> float:
        """Liefert den tatsächlich zu schreibenden Sollwert.

        Reduzierungen (inkl. Pausieren auf 0 A) werden immer sofort
        durchgelassen – das ist die sichere Richtung. Erhöhungen unterliegen
        Mindeständerung und Haltezeit.
        """
        now = self._time()
        state = self._state.get(station_id)

        if state is None:
            self._state[station_id] = _SetpointState(target, now)
            return target

        diff = target - state.value

        # Sicher: Reduzierung immer sofort anwenden
        if diff < 0:
            if abs(diff) < self.min_change_a and target > 0:
                # Winzige Reduzierung glätten, aber echtes Pausieren (0) zulassen
                return state.value
            state.value = target
            state.changed_at = now
            return target

        # Erhöhung: nur bei ausreichender Änderung und nach Haltezeit
        if diff < self.min_change_a:
            return state.value
        if now - state.changed_at < self.min_hold_s:
            return state.value

        state.value = target
        state.changed_at = now
        return target

    def current(self, station_id: int) -> float | None:
        """Zuletzt bekannter Sollwert einer Station (oder None)."""
        state = self._state.get(station_id)
        return state.value if state else None
