"""MQTT-Anbindung: Status veröffentlichen (z. B. für Home Assistant, Node-RED).

Läuft, wie ``app.loadmanager.cloud_relay``, als eigener, unabhängiger
Hintergrund-Task. Jeder Verbindungsfehler wird geloggt und geschluckt – die
lokale Regelung darf davon NIE beeinflusst werden. Bewusst nur Publish,
kein Empfang von Steuerbefehlen (siehe README: Sollwerte per MQTT setzen
wäre eine eigene Sicherheits-/Validierungsfrage und ist nicht im Scope).
"""

from __future__ import annotations

import asyncio
import json
import logging

import aiomqtt

from app.db import session_scope
from app.loadmanager.engine import PHASES
from app.loadmanager.status_builder import build_system_status
from app.models import GlobalConfig
from app.schemas import SystemStatus

log = logging.getLogger("lastmanagement.mqtt_publisher")

_DEFAULT_RETRY_INTERVAL_S = 30.0
_DEVICE_INFO = {"identifiers": ["voltibus"], "name": "Voltibus", "manufacturer": "Voltibus"}


def _state_messages(prefix: str, status: SystemStatus) -> list[tuple[str, str]]:
    """(topic, payload) für den aktuellen Systemzustand + je Ladepunkt."""
    msgs: list[tuple[str, str]] = []
    for phase in PHASES:
        msgs.append((f"{prefix}/status/phase_load_a/{phase}", str(status.phase_load_a.get(phase, 0.0))))
        msgs.append((f"{prefix}/status/phase_available_a/{phase}", str(status.phase_available_a.get(phase, 0.0))))
        msgs.append((f"{prefix}/status/phase_surplus_a/{phase}", str(status.phase_surplus_a.get(phase, 0.0))))
    msgs.append((f"{prefix}/status/effective_limit_current_a", str(status.effective_limit_current_a)))
    msgs.append((f"{prefix}/status/management_mode", status.management_mode.value))
    msgs.append((f"{prefix}/status/distribution_strategy", status.distribution_strategy.value))
    msgs.append((f"{prefix}/status/active_charge_points", str(status.active_charge_points)))
    msgs.append((f"{prefix}/status/total_charge_points", str(status.total_charge_points)))
    msgs.append((f"{prefix}/status/en14a_active", "true" if status.en14a_active else "false"))

    for cp in status.charge_points:
        base = f"{prefix}/chargepoints/{cp.charge_point_id}"
        msgs.append((f"{base}/online", "true" if cp.online else "false"))
        msgs.append((f"{base}/setpoint_a", "" if cp.setpoint_a is None else str(cp.setpoint_a)))
        values = cp.values or {}
        for key, topic_suffix in (
            ("current_l1", "current_l1"),
            ("current_l2", "current_l2"),
            ("current_l3", "current_l3"),
            ("active_power", "power"),
            ("energy_total", "energy_total"),
            ("charge_status_text", "status_text"),
        ):
            if values.get(key) is not None:
                msgs.append((f"{base}/{topic_suffix}", str(values[key])))
    return msgs


def _discovery_messages(prefix: str, status: SystemStatus) -> list[tuple[str, str]]:
    """Home-Assistant-MQTT-Discovery-Konfigurationen (retained)."""
    msgs: list[tuple[str, str]] = []

    def sensor(object_id: str, name: str, state_topic: str, unit: str | None = None) -> None:
        config = {
            "name": name,
            "state_topic": state_topic,
            "unique_id": f"voltibus_{object_id}",
            "device": _DEVICE_INFO,
        }
        if unit:
            config["unit_of_measurement"] = unit
        msgs.append((f"homeassistant/sensor/voltibus_{object_id}/config", json.dumps(config)))

    for phase in PHASES:
        p = phase.lower()
        sensor(f"load_{p}", f"Last {phase}", f"{prefix}/status/phase_load_a/{phase}", "A")
        sensor(f"available_{p}", f"Verfügbar {phase}", f"{prefix}/status/phase_available_a/{phase}", "A")
        sensor(f"surplus_{p}", f"PV-Überschuss {phase}", f"{prefix}/status/phase_surplus_a/{phase}", "A")
    sensor("effective_limit", "Effektive Grenze", f"{prefix}/status/effective_limit_current_a", "A")
    sensor("active_charge_points", "Aktive Ladepunkte", f"{prefix}/status/active_charge_points")
    sensor("total_charge_points", "Ladepunkte gesamt", f"{prefix}/status/total_charge_points")

    for cp in status.charge_points:
        base = f"{prefix}/chargepoints/{cp.charge_point_id}"
        cid = cp.charge_point_id
        sensor(f"cp{cid}_setpoint", f"{cp.name} Sollstrom", f"{base}/setpoint_a", "A")
        values = cp.values or {}
        if values.get("current_l1") is not None:
            sensor(f"cp{cid}_current_l1", f"{cp.name} L1", f"{base}/current_l1", "A")
        if values.get("current_l2") is not None:
            sensor(f"cp{cid}_current_l2", f"{cp.name} L2", f"{base}/current_l2", "A")
        if values.get("current_l3") is not None:
            sensor(f"cp{cid}_current_l3", f"{cp.name} L3", f"{base}/current_l3", "A")
        if values.get("active_power") is not None:
            sensor(f"cp{cid}_power", f"{cp.name} Leistung", f"{base}/power", "W")
        if values.get("energy_total") is not None:
            sensor(f"cp{cid}_energy", f"{cp.name} Energie", f"{base}/energy_total", "kWh")
    return msgs


class MqttPublisherService:
    """Eigenständiger Hintergrund-Task, unabhängig vom Regelzyklus."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="mqtt_publisher")
            log.info("MQTT-Anbindung gestartet")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
        log.info("MQTT-Anbindung gestoppt")

    async def _run(self) -> None:
        while not self._stop.is_set():
            interval = await self._publish_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    def _load_config(self, session):
        cfg = GlobalConfig.get_or_create(session)
        status = build_system_status(session)
        return cfg, status

    async def _publish_once(self) -> float:
        """Ein Sendeversuch. Gibt das bis zum nächsten Versuch zu wartende
        Intervall zurück (aus der Konfiguration, sonst ein Standardwert)."""
        try:
            with session_scope() as session:
                cfg, status = self._load_config(session)
                interval = cfg.mqtt_interval_s
                if not cfg.mqtt_enabled or not cfg.mqtt_host:
                    return interval
                host, port = cfg.mqtt_host, cfg.mqtt_port
                username, password = cfg.mqtt_username, cfg.mqtt_password
                prefix = cfg.mqtt_topic_prefix
                ha_discovery = cfg.mqtt_ha_discovery
        except Exception:  # noqa: BLE001 - darf den Task niemals beenden
            log.exception("MQTT: Fehler beim Vorbereiten des Status")
            return _DEFAULT_RETRY_INTERVAL_S

        try:
            await self._connect_and_publish(host, port, username, password, prefix, status, ha_discovery)
        except Exception as exc:  # noqa: BLE001 - jede Störung ist rein informativ
            log.warning("MQTT: Senden fehlgeschlagen: %s", exc)

        return interval

    @staticmethod
    async def _connect_and_publish(host, port, username, password, prefix, status, ha_discovery) -> None:
        async with aiomqtt.Client(hostname=host, port=port, username=username, password=password) as client:
            if ha_discovery:
                for topic, payload in _discovery_messages(prefix, status):
                    await client.publish(topic, payload, retain=True)
            for topic, payload in _state_messages(prefix, status):
                await client.publish(topic, payload, retain=False)

    async def test_now(self) -> dict:
        """Sofortiger, expliziter Sendeversuch für den "Verbindung jetzt
        testen"-Button. Ignoriert bewusst ``mqtt_enabled``, damit
        Zugangsdaten geprüft werden können, bevor die Anbindung aktiviert
        wird."""
        with session_scope() as session:
            cfg, status = self._load_config(session)
            if not cfg.mqtt_host:
                return {"ok": False, "error": "MQTT-Host fehlt"}
            host, port = cfg.mqtt_host, cfg.mqtt_port
            username, password = cfg.mqtt_username, cfg.mqtt_password
            prefix = cfg.mqtt_topic_prefix
            ha_discovery = cfg.mqtt_ha_discovery

        try:
            await self._connect_and_publish(host, port, username, password, prefix, status, ha_discovery)
            return {"ok": True, "error": None}
        except Exception as exc:  # noqa: BLE001 - Fehlermeldung geht direkt an die UI
            return {"ok": False, "error": str(exc)}


service = MqttPublisherService()
