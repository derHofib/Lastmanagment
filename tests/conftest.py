"""Gemeinsame Test-Fixtures.

Wichtig: Die Datenbank-URL wird auf eine temporäre Datei gesetzt, *bevor*
``app.db`` importiert wird (Engine ist ein Modul-Singleton).
"""

import os
import tempfile

# Temporäre DB je Testlauf – vor jeglichem App-Import setzen
_TMP_DB = os.path.join(tempfile.gettempdir(), "lm_test_%d.db" % os.getpid())
os.environ["LM_DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["LM_LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    """FastAPI-TestClient mit frischer DB und *ohne* laufenden Regelzyklus."""
    # Regelzyklus im Test nicht starten (kein echtes Modbus nötig)
    from app.loadmanager.loop import service

    monkeypatch.setattr(service, "start", lambda: None)

    from app.db import Base, engine, init_db

    # Frische Tabellen
    Base.metadata.drop_all(bind=engine)
    init_db()

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c

    Base.metadata.drop_all(bind=engine)


def teardown_module(module):  # pragma: no cover
    try:
        os.remove(_TMP_DB)
    except OSError:
        pass
