"""Datenbank-Setup (SQLAlchemy 2.0 + SQLite).

SQLite ist leichtgewichtig und benötigt keinen separaten Server – ideal für
den Raspberry Pi. Für den nebenläufigen Betrieb (API-Requests + Regelzyklus)
aktivieren wir den WAL-Modus und erlauben Zugriff aus mehreren Threads.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.base import Base


def _make_engine(database_url: str) -> Engine:
    connect_args = {}
    if database_url.startswith("sqlite"):
        # check_same_thread=False: Zugriff aus API-Thread und Regel-Task
        connect_args = {"check_same_thread": False, "timeout": 10}
    engine = create_engine(
        database_url,
        connect_args=connect_args,
        future=True,
    )
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            # WAL erlaubt gleichzeitiges Lesen/Schreiben und ist robuster
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


engine: Engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Erzeugt alle Tabellen, falls sie noch nicht existieren."""
    # Import stellt sicher, dass alle Modelle registriert sind
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_new_columns()
    _migrate_charge_points()


# Leichtgewichtiger Kompatibilitäts-Check ohne volles Migrationsframework:
# create_all() legt fehlende TABELLEN an, ergänzt aber keine SPALTEN an
# bereits existierenden Tabellen. Diese Spalten kamen nach dem ersten Release
# hinzu (Verteilungshierarchie / Verteiler-Strategie) und müssen bei
# bestehenden SQLite-Dateien nachträglich ergänzt werden.
_NEW_COLUMNS = {
    "charging_stations": [
        ("distribution_board_id", "INTEGER REFERENCES distribution_boards(id)"),
        ("circuit_breaker_a", "FLOAT"),
        ("canvas_x", "FLOAT"),
        ("canvas_y", "FLOAT"),
    ],
    "distribution_boards": [
        ("strategy", "VARCHAR"),
        ("canvas_x", "FLOAT"),
        ("canvas_y", "FLOAT"),
    ],
    "global_config": [
        ("cloud_relay_enabled", "BOOLEAN DEFAULT 0"),
        ("cloud_relay_url", "VARCHAR"),
        ("cloud_relay_token", "VARCHAR"),
        ("cloud_relay_interval_s", "FLOAT DEFAULT 30.0"),
        ("mqtt_enabled", "BOOLEAN DEFAULT 0"),
        ("mqtt_host", "VARCHAR"),
        ("mqtt_port", "INTEGER DEFAULT 1883"),
        ("mqtt_username", "VARCHAR"),
        ("mqtt_password", "VARCHAR"),
        ("mqtt_topic_prefix", "VARCHAR DEFAULT 'voltibus'"),
        ("mqtt_interval_s", "FLOAT DEFAULT 10.0"),
        ("mqtt_ha_discovery", "BOOLEAN DEFAULT 1"),
        ("dashboard_layout", "TEXT"),
    ],
}


def _ensure_new_columns() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.connect() as conn:
        for table, columns in _NEW_COLUMNS.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, ddl_type in columns:
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")
        conn.commit()


# Datenmigration: ChargingStation vermischte früher physische Verbindung und
# Regel-Parameter eines einzelnen Ladepunkts; seit der Ladepunkt-Trennung
# leben Priorität/Phasen/Min-Max-Strom/Verteiler/Absicherung/Fail-Safe in
# einer eigenen ChargePoint-Zeile. Bestehende Stationen bekommen hier
# automatisch genau einen ChargePoint (connector_suffix="") mit den alten
# Werten. Idempotent (nur Stationen ohne vorhandenen ChargePoint) und
# gefahrlos: Falls die alten Spalten nicht mehr existieren (frische
# Installation), ist nichts zu migrieren.
_LEGACY_STATION_COLUMNS = {
    "phase_config", "priority", "max_current_a", "min_current_a",
    "distribution_board_id", "circuit_breaker_a", "enabled", "safe_state",
}


def _migrate_charge_points() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.connect() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(charging_stations)")}
        if not _LEGACY_STATION_COLUMNS.issubset(existing):
            return
        conn.exec_driver_sql("""
            INSERT INTO charge_points (
                station_id, connector_suffix, name, phase_config, priority,
                max_current_a, min_current_a, distribution_board_id,
                circuit_breaker_a, enabled, safe_state, pv_surplus_only
            )
            SELECT cs.id, '', cs.name, cs.phase_config, cs.priority,
                   cs.max_current_a, cs.min_current_a, cs.distribution_board_id,
                   cs.circuit_breaker_a, cs.enabled, cs.safe_state, 0
            FROM charging_stations cs
            WHERE cs.id NOT IN (SELECT station_id FROM charge_points)
        """)
        conn.commit()


def get_session() -> Iterator[Session]:
    """FastAPI-Dependency: liefert eine Session pro Request."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Kontextmanager für Hintergrund-Tasks (Regelzyklus)."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
