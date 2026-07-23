"""Voltibus Cloud: FastAPI-Anwendung (Registrierung/Login, Installationen, Ingest)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from cloud import __version__
from cloud.api import auth_router, ingest_router, installations_router
from cloud.config import settings
from cloud.db import init_db

log = logging.getLogger("voltibus_cloud")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=settings.log_level)
    init_db()
    if settings.jwt_secret_is_random:
        log.warning(
            "LMC_JWT_SECRET ist nicht gesetzt – es wurde ein zufälliger Schlüssel "
            "erzeugt. Bestehende Sessions gehen bei jedem Neustart verloren. Für "
            "den Produktivbetrieb LMC_JWT_SECRET fest setzen (siehe README-cloud.md)."
        )
    log.info("Voltibus Cloud %s startet", __version__)
    yield
    log.info("Voltibus Cloud beendet")


app = FastAPI(
    title="Voltibus Cloud",
    version=__version__,
    description="Registrierung, Login und Fernansicht eigener Voltibus-Installationen.",
    lifespan=lifespan,
)

app.include_router(auth_router)
app.include_router(installations_router)
app.include_router(ingest_router)


@app.get("/healthz", tags=["System"])
def healthz():
    return {"status": "ok", "version": __version__}


if STATIC_DIR.exists():
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
