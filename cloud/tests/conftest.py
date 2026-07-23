"""Gemeinsame Test-Fixtures für den Cloud-Dienst.

Wichtig: Die Datenbank-URL wird auf eine temporäre Datei gesetzt, *bevor*
``cloud.db`` importiert wird (Engine ist ein Modul-Singleton) – gleiches
Muster wie ``tests/conftest.py`` für das Hauptprojekt.
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.gettempdir(), "lmc_test_%d.db" % os.getpid())
os.environ["LMC_DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["LMC_JWT_SECRET"] = "test-secret-nicht-fuer-produktion"
os.environ["LMC_LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402


@pytest.fixture()
def client():
    """FastAPI-TestClient mit frischer DB je Test."""
    from cloud.db import Base, engine, init_db

    Base.metadata.drop_all(bind=engine)
    init_db()

    from fastapi.testclient import TestClient

    from cloud.main import app

    with TestClient(app) as c:
        yield c

    Base.metadata.drop_all(bind=engine)


def teardown_module(module):  # pragma: no cover
    try:
        os.remove(_TMP_DB)
    except OSError:
        pass
