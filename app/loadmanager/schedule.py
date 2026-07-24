"""Wiederkehrende Sperrfenster für Ladepunkte (Zeitsteuerung).

Reine, zustandsfreie Logik (keine DB/async-Abhängigkeit), damit sie
deterministisch mit injizierter "aktueller Zeit" testbar ist. Ergänzt das
bereits vorhandene manuelle Ein-/Ausschalten (``ChargePoint.enabled``) um
automatische, wiederkehrende Sperrzeiten (z. B. "täglich 22:00–06:00 aus").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time


@dataclass(frozen=True)
class ScheduleWindow:
    """Ein wiederkehrendes Sperrfenster.

    ``weekdays_mask``: Bit 0 = Montag ... Bit 6 = Sonntag (1 = Fenster gilt
    an diesem Tag). Bezieht sich auf den Tag, an dem das Fenster BEGINNT –
    ein Fenster 22:00–06:00 an "Montag" sperrt bis Dienstag 06:00.
    """

    weekdays_mask: int
    start_time: time
    end_time: time


def is_blocked(windows: list[ScheduleWindow], now: datetime) -> bool:
    """Prüft, ob ``now`` (lokale Zeit) in einem der Sperrfenster liegt.

    Bei einem Fenster über Mitternacht (z. B. 22:00–06:00) gehört der Teil
    nach Mitternacht noch zum Fenster des VORTAGS – daher wird für diesen
    Teil das Wochentag-Bit des Vortags geprüft, nicht des aktuellen Tages.
    """
    weekday = now.weekday()  # Montag=0 ... Sonntag=6
    prev_weekday = (weekday - 1) % 7
    current = now.time()
    for w in windows:
        if w.start_time <= w.end_time:
            if (w.weekdays_mask & (1 << weekday)) and w.start_time <= current < w.end_time:
                return True
        else:
            # Fenster über Mitternacht: später Teil gehört zum Starttag,
            # früher Teil (nach Mitternacht) noch zum Fenster des Vortags.
            if (w.weekdays_mask & (1 << weekday)) and current >= w.start_time:
                return True
            if (w.weekdays_mask & (1 << prev_weekday)) and current < w.end_time:
                return True
    return False
