"""FastAPI-Anwendung: API, statisches Web-UI und Regelzyklus-Lebenszyklus."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import (
    boards_router,
    charge_points_router,
    config_router,
    profiles_router,
    stations_router,
    status_router,
)
from app.db import init_db
from app.loadmanager.loop import service
from app.logging_config import setup_logging

log = logging.getLogger("lastmanagement")

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialisiert DB und startet/stoppt den Regelzyklus mit der App."""
    setup_logging()
    init_db()
    log.info("Lastmanagement %s startet", __version__)
    service.start()
    try:
        yield
    finally:
        await service.stop()
        log.info("Lastmanagement beendet")


app = FastAPI(
    title="Lastmanagement für Ladestationen",
    version=__version__,
    description="Dynamisches/statisches Lastmanagement von EV-Ladestationen über Modbus TCP.",
    lifespan=lifespan,
)

app.include_router(profiles_router)
app.include_router(stations_router)
app.include_router(charge_points_router)
app.include_router(boards_router)
app.include_router(config_router)
app.include_router(status_router)


@app.get("/healthz", tags=["System"])
def healthz():
    """Einfacher Health-Check für Monitoring/systemd."""
    return {"status": "ok", "version": __version__}


# Statisches Web-UI (Dashboard) unter / bereitstellen
if STATIC_DIR.exists():
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
