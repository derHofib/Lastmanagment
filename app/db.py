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
