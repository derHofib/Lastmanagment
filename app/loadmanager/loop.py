"""Regelzyklus: Polling, Lastverteilung, Fail-Safe und Sollwert-Ausgabe.

Der :class:`LoadManagerService` ist der zentrale Hintergrund-Task. Er wird
beim App-Start gestartet und läuft mit konfigurierbarer Zykluszeit. Jede
Station wird isoliert und mit eigenem Timeout gepollt – ein hängender Client
blockiert den Zyklus nicht. Alle Ausnahmen werden pro Zyklus/Station gefangen,
damit der Regelbetrieb im Dauerbetrieb stabil bleibt.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings
from app.db import session_scope
from app.loadmanager import safety
from app.loadmanager.engine import PHASES, AllocStation, allocate, phase_totals
from app.loadmanager.smoothing import SetpointSmoother
from app.models import ChargingStation, GlobalConfig, Measurement
from app.models.base import ManagementMode
from app.modbus.client import ModbusError, StationClient
from app.modbus.runtime import ProfileSpec, StationSpec

log = logging.getLogger("lastmanagement.loop")

# Persistierte Messwert-Schlüssel (Historie)
_PERSIST_KEYS = ("current_l1", "current_l2", "current_l3", "active_power", "energy_total")

# Statuswörter zur Erkennung eines ladebereiten/ladenden Fahrzeugs
_IDLE_WORDS = ("verfügbar", "available", "frei", "kein", "idle", "getrennt", "disconnect", "none")
_ERROR_WORDS = ("fehler", "error", "fault", "störung")


def _wants_charge(values: dict) -> bool:
    """Heuristik: Will/kann die Station laden? (Fahrzeug verbunden)."""
    text = values.get("charge_status_text")
    if isinstance(text, str):
        low = text.lower()
        if any(w in low for w in _ERROR_WORDS):
            return False
        if any(w in low for w in _IDLE_WORDS):
            return False
        return True
    # Kein Status -> anhand gemessenem Strom entscheiden
    has_current = any(k in values for k in ("current_l1", "current_l2", "current_l3"))
    if has_current:
        total = sum(
            float(values.get(k, 0.0) or 0.0)
            for k in ("current_l1", "current_l2", "current_l3")
        )
        return total > 0.1
    # Keinerlei Info -> aktiv annehmen (minimalistisches Profil)
    return True


class LoadManagerService:
    """Verwaltet Clients, Zustand und den periodischen Regelzyklus."""

    def __init__(self):
        self._clients: dict[int, StationClient] = {}
        self._smoother = SetpointSmoother()
        self._last_success: dict[int, float] = {}
        self._last_written: dict[int, tuple[float, int | None]] = {}
        self._plug_order: dict[int, int] = {}
        self._plug_seq = 0
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._tz = ZoneInfo(settings.timezone)
        # Momentaufnahme für die API (thread-/task-sicher genug via Zuweisung)
        self.snapshot: dict = {
            "stations": {},
            "phase_load_a": {p: 0.0 for p in PHASES},
            "phase_available_a": {p: 0.0 for p in PHASES},
            "effective_limit_current_a": 0.0,
            "en14a_active": False,
            "active_stations": 0,
            "last_cycle": None,
            "meter_ok": True,
        }

    # --- Lebenszyklus ------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="loadmanager")
            log.info("Lastmanagement-Regelzyklus gestartet")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
        log.info("Lastmanagement-Regelzyklus gestoppt")

    async def _run(self) -> None:
        while not self._stop.is_set():
            interval = settings.default_poll_interval_s
            try:
                interval = await self._cycle()
            except Exception:  # pragma: no cover - Schutz des Dauerbetriebs
                log.exception("Unerwarteter Fehler im Regelzyklus")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(0.5, interval))
            except asyncio.TimeoutError:
                pass

    # --- Datenbeschaffung --------------------------------------------------

    def _load_config(self) -> tuple[dict, list[StationSpec], StationSpec | None]:
        """Lädt Konfiguration, aktive Stationen und optional den Zähler (blockierend)."""
        with session_scope() as session:
            cfg = GlobalConfig.get_or_create(session)
            cfg_dict = {
                "grid_limit_current_a": cfg.grid_limit_current_a,
                "management_mode": cfg.management_mode,
                "distribution_strategy": cfg.distribution_strategy,
                "poll_interval_s": cfg.poll_interval_s,
                "setpoint_min_change_a": cfg.setpoint_min_change_a,
                "setpoint_min_hold_s": cfg.setpoint_min_hold_s,
                "fail_safe_after_s": cfg.fail_safe_after_s,
                "dynamic_meter_enabled": cfg.dynamic_meter_enabled,
                "en14a_enabled": cfg.en14a_enabled,
                "en14a_active": cfg.en14a_active,
                "en14a_limit_current_a": cfg.en14a_limit_current_a,
            }
            stations = (
                session.query(ChargingStation)
                .filter(ChargingStation.enabled.is_(True))
                .all()
            )
            specs = [StationSpec.from_station(s) for s in stations]

            meter_spec: StationSpec | None = None
            if cfg.dynamic_meter_enabled and cfg.meter_profile_id and cfg.meter_ip_address:
                from app.models import DeviceProfile

                profile = session.get(DeviceProfile, cfg.meter_profile_id)
                if profile is not None:
                    meter_spec = StationSpec(
                        id=-1,
                        name="Netzanschlusszähler",
                        ip_address=cfg.meter_ip_address,
                        tcp_port=cfg.meter_tcp_port,
                        unit_id=cfg.meter_unit_id,
                        profile=ProfileSpec.from_profile(profile),
                        phases=PHASES,
                        priority=0,
                        max_current_a=0.0,
                        min_current_a=0.0,
                        safe_state="block",
                    )
        return cfg_dict, specs, meter_spec

    def _sync_clients(self, specs: list[StationSpec], meter: StationSpec | None) -> None:
        """Erzeugt/entfernt Clients passend zur aktuellen Stationsliste."""
        wanted = {s.id: s for s in specs}
        if meter is not None:
            wanted[meter.id] = meter
        # Neue anlegen / geänderte Verbindungsdaten aktualisieren
        for sid, spec in wanted.items():
            existing = self._clients.get(sid)
            if (
                existing is None
                or existing.station.ip_address != spec.ip_address
                or existing.station.tcp_port != spec.tcp_port
                or existing.station.unit_id != spec.unit_id
            ):
                if existing is not None:
                    asyncio.create_task(existing.close())
                self._clients[sid] = StationClient(
                    spec, timeout_s=settings.modbus_timeout_s, retries=settings.modbus_retries
                )
            else:
                # Profil/Parameter aktualisieren (Registeränderungen übernehmen)
                existing.station = spec
        # Verwaiste entfernen
        for sid in list(self._clients):
            if sid not in wanted:
                asyncio.create_task(self._clients[sid].close())
                del self._clients[sid]
                self._last_success.pop(sid, None)
                self._plug_order.pop(sid, None)

    async def _poll_station(self, spec: StationSpec) -> tuple[int, dict, str | None]:
        """Pollt eine Station mit eigenem Timeout, isoliert von den übrigen."""
        client = self._clients[spec.id]
        try:
            values = await asyncio.wait_for(
                client.read_all(), timeout=settings.modbus_timeout_s * (settings.modbus_retries + 1) + 1
            )
            return spec.id, values, None
        except (ModbusError, asyncio.TimeoutError, OSError) as exc:
            return spec.id, {}, str(exc)
        except Exception as exc:  # pragma: no cover
            return spec.id, {}, f"Unerwartet: {exc}"

    # --- Ein Regelzyklus ---------------------------------------------------

    async def _cycle(self) -> float:
        cfg, specs, meter = await asyncio.to_thread(self._load_config)
        self._sync_clients(specs, meter)

        # Hysterese-Parameter aktualisieren
        self._smoother.min_change_a = cfg["setpoint_min_change_a"]
        self._smoother.min_hold_s = cfg["setpoint_min_hold_s"]

        now = time.monotonic()

        # 1) Alle Stationen parallel pollen
        results = await asyncio.gather(*(self._poll_station(s) for s in specs))
        values_by_id: dict[int, dict] = {}
        error_by_id: dict[int, str | None] = {}
        for sid, values, error in results:
            values_by_id[sid] = values
            error_by_id[sid] = error
            if error is None:
                self._last_success[sid] = now

        # 2) Online-/Offline-Status anhand Fail-Safe-Zeitfenster
        online_ids: set[int] = set()
        offline_specs: list[StationSpec] = []
        spec_by_id = {s.id: s for s in specs}
        for spec in specs:
            last = self._last_success.get(spec.id)
            is_online = error_by_id.get(spec.id) is None and bool(values_by_id.get(spec.id))
            within = last is not None and (now - last) <= cfg["fail_safe_after_s"]
            if is_online:
                online_ids.add(spec.id)
            elif not within:
                # Kommunikationsverlust über Fail-Safe-Zeit -> sicherer Zustand
                offline_specs.append(spec)
                self._smoother.reset(spec.id)

        # 3) Effektive Grenze bestimmen (§14a hat Vorrang)
        effective_limit = cfg["grid_limit_current_a"]
        en14a_active = bool(cfg["en14a_enabled"] and cfg["en14a_active"])
        if en14a_active:
            effective_limit = min(effective_limit, cfg["en14a_limit_current_a"])
        capacity = {p: effective_limit for p in PHASES}

        # 4) Dynamisches Lastmanagement: Grundlast des Hauses abziehen
        meter_ok = True
        if cfg["management_mode"] == ManagementMode.DYNAMIC and meter is not None:
            capacity, meter_ok = await self._apply_dynamic_meter(
                meter, capacity, values_by_id, spec_by_id, online_ids
            )

        # 5) Reserven für unerreichbare Stationen abziehen (konservativ)
        capacity = safety.subtract_reservations(capacity, offline_specs)
        capacity = safety.clamp_capacity(capacity)

        # 6) Ladebereite Online-Stationen ermitteln + FIFO-Reihenfolge pflegen
        alloc_stations: list[AllocStation] = []
        active_specs: list[StationSpec] = []
        for spec in specs:
            if spec.id not in online_ids:
                continue
            if not _wants_charge(values_by_id[spec.id]):
                self._plug_order.pop(spec.id, None)
                continue
            if spec.id not in self._plug_order:
                self._plug_seq += 1
                self._plug_order[spec.id] = self._plug_seq
            active_specs.append(spec)
            alloc_stations.append(
                AllocStation(
                    id=spec.id,
                    phases=spec.phases,
                    min_current_a=spec.min_current_a,
                    max_current_a=spec.max_current_a,
                    priority=spec.priority,
                    order=self._plug_order[spec.id],
                )
            )

        # 7) Verteilung berechnen (harte Grenzgarantie)
        targets = allocate(alloc_stations, capacity, cfg["distribution_strategy"])

        # 8) Sollwerte glätten und schreiben
        await self._write_setpoints(active_specs, targets)

        # 9) Messwerte persistieren + Momentaufnahme aktualisieren
        await asyncio.to_thread(self._persist, values_by_id, online_ids)
        self._update_snapshot(
            specs, values_by_id, error_by_id, online_ids, targets,
            capacity, effective_limit, en14a_active, alloc_stations, meter_ok
        )
        return max(0.5, cfg["poll_interval_s"])

    async def _apply_dynamic_meter(self, meter, capacity, values_by_id, spec_by_id, online_ids):
        """Berechnet die verfügbare Kapazität abzüglich der Haus-Grundlast."""
        try:
            meter_vals = await asyncio.wait_for(
                self._clients[meter.id].read_all(), timeout=settings.modbus_timeout_s + 1
            )
        except (ModbusError, asyncio.TimeoutError, OSError) as exc:
            # Fail-Safe: Zähler nicht lesbar -> konservativ nur Minimalbetrieb
            log.warning("Netzanschlusszähler nicht lesbar (%s) – konservative Begrenzung", exc)
            self.snapshot["meter_values"] = {}
            # Kapazität auf 0 setzen: nur bereits laufende Boxen behalten Minimalstrom
            return {p: 0.0 for p in PHASES}, False

        self.snapshot["meter_values"] = meter_vals
        result = {}
        for i, phase in enumerate(PHASES, start=1):
            total = float(meter_vals.get(f"current_l{i}", meter_vals.get("current", 0.0)) or 0.0)
            # Anteil, den unsere Wallboxen aktuell auf dieser Phase ziehen
            wb = 0.0
            for sid in online_ids:
                spec = spec_by_id[sid]
                if phase in spec.phases:
                    wb += float(values_by_id[sid].get(f"current_l{i}", 0.0) or 0.0)
            base_load = max(0.0, total - wb)  # Grundlast ohne Wallboxen
            result[phase] = max(0.0, capacity[phase] - base_load)
        return result, True

    async def _write_setpoints(self, specs: list[StationSpec], targets: dict[int, float]) -> None:
        """Schreibt Sollstrom + enable, geglättet und nur bei Änderung."""
        for spec in specs:
            client = self._clients.get(spec.id)
            if client is None:
                continue
            target = targets.get(spec.id, 0.0)
            smoothed = self._smoother.desired(spec.id, target)

            set_spec = spec.profile.registers.get("set_current")
            enable_spec = spec.profile.registers.get("enable")
            enable_val = None
            if smoothed <= 0.0:
                enable_val = 0  # pausieren
            else:
                enable_val = 1

            last = self._last_written.get(spec.id)
            new_state = (round(smoothed, 3), enable_val)
            if last == new_state:
                continue  # keine Änderung -> Bus schonen, Relais-Flattern vermeiden

            try:
                if enable_spec is not None:
                    await client.write_register(enable_spec, enable_val)
                if set_spec is not None and smoothed > 0.0:
                    await client.write_register(set_spec, smoothed)
                elif set_spec is not None and enable_spec is None and smoothed <= 0.0:
                    # Kein enable-Register: soweit möglich herunterregeln
                    log.warning(
                        "Station %s: kein enable-Register – Pausieren nur via Minimalstrom möglich",
                        spec.name,
                    )
                self._last_written[spec.id] = new_state
                reason = "pausiert (unter Minimalstrom)" if smoothed <= 0 else f"Zuteilung {smoothed:.1f} A"
                log.info("Sollwert Station %s -> %.1f A (%s)", spec.name, smoothed, reason)
            except (ModbusError, asyncio.TimeoutError, OSError) as exc:
                log.warning("Station %s: Sollwert-Schreiben fehlgeschlagen: %s", spec.name, exc)

    # --- Persistenz & Momentaufnahme --------------------------------------

    def _persist(self, values_by_id: dict[int, dict], online_ids: set[int]) -> None:
        """Persistiert ausgewählte Messwerte der Online-Stationen."""
        ts = datetime.now(self._tz)
        rows = []
        for sid in online_ids:
            for key in _PERSIST_KEYS:
                val = values_by_id.get(sid, {}).get(key)
                if isinstance(val, (int, float)):
                    rows.append(Measurement(station_id=sid, timestamp=ts, key=key, value=float(val)))
        if not rows:
            return
        try:
            with session_scope() as session:
                session.add_all(rows)
        except Exception:  # pragma: no cover
            log.exception("Messwerte konnten nicht gespeichert werden")

    def _update_snapshot(self, specs, values_by_id, error_by_id, online_ids, targets,
                         capacity, effective_limit, en14a_active, alloc_stations, meter_ok):
        stations_snap = {}
        for spec in specs:
            stations_snap[spec.id] = {
                "station_id": spec.id,
                "name": spec.name,
                "online": spec.id in online_ids,
                "values": values_by_id.get(spec.id, {}),
                "setpoint_a": targets.get(spec.id),
                "error": error_by_id.get(spec.id),
            }
        load = phase_totals(targets, alloc_stations)
        self.snapshot = {
            **self.snapshot,
            "stations": stations_snap,
            "phase_load_a": load,
            "phase_available_a": capacity,
            "effective_limit_current_a": effective_limit,
            "en14a_active": en14a_active,
            "active_stations": len(alloc_stations),
            "last_cycle": datetime.now(self._tz).isoformat(),
            "meter_ok": meter_ok,
        }


# Modulweite Singleton-Instanz (wird von main.py gestartet)
service = LoadManagerService()
