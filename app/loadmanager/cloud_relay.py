"""Cloud-Anbindung (optionales "Phone-Home" an Voltibus Cloud).

Sendet periodisch den lokalen Systemzustand (wie ``GET /api/status``) an
einen selbst gehosteten Cloud-Dienst (siehe ``cloud/``), damit die eigene
Installation auch remote einsehbar ist. Rein additiv: Jeder Fehler (Cloud
nicht erreichbar, falsches Token, Timeout) wird geloggt und geschluckt – die
lokale Regelung (``app.loadmanager.loop``) darf davon NIE beeinflusst
werden. Läuft deshalb als eigener, von ihr unabhängiger asyncio-Task.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.db import session_scope
from app.loadmanager.status_builder import build_system_status
from app.models import GlobalConfig

log = logging.getLogger("lastmanagement.cloud_relay")

_DEFAULT_RETRY_INTERVAL_S = 30.0


class CloudRelayService:
    """Eigenständiger Hintergrund-Task, unabhängig vom Regelzyklus."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="cloud_relay")
            log.info("Cloud-Anbindung gestartet")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
        log.info("Cloud-Anbindung gestoppt")

    async def _run(self) -> None:
        while not self._stop.is_set():
            interval = await self._send_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _send_once(self) -> float:
        """Ein Sendeversuch. Gibt das bis zum nächsten Versuch zu wartende
        Intervall zurück (aus der Konfiguration, sonst ein Standardwert)."""
        try:
            with session_scope() as session:
                cfg = GlobalConfig.get_or_create(session)
                interval = cfg.cloud_relay_interval_s
                if not cfg.cloud_relay_enabled or not cfg.cloud_relay_url or not cfg.cloud_relay_token:
                    return interval
                url = cfg.cloud_relay_url.rstrip("/") + "/api/ingest"
                token = cfg.cloud_relay_token
                payload = build_system_status(session).model_dump(mode="json")
        except Exception:  # noqa: BLE001 - darf den Task niemals beenden
            log.exception("Cloud-Anbindung: Fehler beim Vorbereiten des Status")
            return _DEFAULT_RETRY_INTERVAL_S

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url, json=payload, headers={"Authorization": f"Bearer {token}"}
                )
            if resp.status_code >= 400:
                log.warning("Cloud-Anbindung: Server antwortete mit Status %s", resp.status_code)
        except Exception as exc:  # noqa: BLE001 - jede Störung ist rein informativ
            log.warning("Cloud-Anbindung: Senden fehlgeschlagen: %s", exc)

        return interval

    async def test_now(self) -> dict:
        """Sofortiger, expliziter Sendeversuch für den "Verbindung jetzt
        testen"-Button. Ignoriert bewusst ``cloud_relay_enabled``, damit
        Zugangsdaten geprüft werden können, bevor die Anbindung aktiviert
        wird."""
        with session_scope() as session:
            cfg = GlobalConfig.get_or_create(session)
            if not cfg.cloud_relay_url or not cfg.cloud_relay_token:
                return {"ok": False, "error": "Cloud-URL oder Token fehlt"}
            url = cfg.cloud_relay_url.rstrip("/") + "/api/ingest"
            token = cfg.cloud_relay_token
            payload = build_system_status(session).model_dump(mode="json")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url, json=payload, headers={"Authorization": f"Bearer {token}"}
                )
            if resp.status_code >= 400:
                return {"ok": False, "error": f"Server antwortete mit Status {resp.status_code}"}
            return {"ok": True, "error": None}
        except Exception as exc:  # noqa: BLE001 - Fehlermeldung geht direkt an die UI
            return {"ok": False, "error": str(exc)}


service = CloudRelayService()
