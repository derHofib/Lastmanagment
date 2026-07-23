"""Einheitliche Zeitstempel für den Cloud-Dienst.

SQLite (über SQLAlchemy) speichert ``DateTime(timezone=True)`` nicht
wirklich zeitzonenbewusst – beim Zurücklesen kommen naive Werte zurück, was
beim Vergleich mit zeitzonenbewussten Werten zu ``TypeError`` führt. Um das
zu vermeiden, wird im gesamten Cloud-Dienst konsequent **naive UTC**
verwendet: erzeugt über diese Hilfsfunktion, gespeichert in normalen
``DateTime``-Spalten (ohne ``timezone=True``).
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
