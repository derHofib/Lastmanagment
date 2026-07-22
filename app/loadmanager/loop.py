"""Regelzyklus: Polling, Lastverteilung, Fail-Safe und Sollwert-Ausgabe.

Der :class:`LoadManagerService` ist der zentrale Hintergrund-Task. Er wird
beim App-Start gestartet und läuft mit konfigurierbarer Zykluszeit. Jede
Station (Modbus-TCP-Verbindung) wird isoliert und mit eigenem Timeout
gepollt – ein hängender Client blockiert den Zyklus nicht. Zuteilung,
Priorität, Zeitpläne, Verteiler und PV-Überschuss wirken auf Ebene der
Ladepunkte (``ChargePoint``) – eine Station kann mehrere Ladepunkte haben
(Doppel-Wallboxen). Alle Ausnahmen werden pro Zyklus/Station gefangen, damit
der Regelbetrieb im Dauerbetrieb stabil bleibt.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import joinedload

from app.config import settings
from app.db import session_scope
from app.loadmanager import safety
from app.loadmanager.engine import (
    PHASES,
    AllocStation,
    BoardNode,
    allocate_tree,
    phase_totals,
    remaining_capacity_tree,
)
from app.loadmanager.schedule import ScheduleWindow, is_blocked
from app.loadmanager.smoothing import SetpointSmoother
from app.models import ChargePoint, ChargingStation, DistributionBoard, GlobalConfig, Measurement
from app.models.base import ManagementMode
from app.modbus.client import ModbusError, StationClient
from app.modbus.runtime import ChargePointSpec, ProfileSpec, StationSpec

log = logging.getLogger("lastmanagement.loop")

# Bekannte, feste semantische Messwert-Schlüssel (siehe README). Werden für
# jeden Ladepunkt anhand seines connector_suffix aus den Rohwerten der
# Station aufgelöst und dabei vom Suffix befreit (siehe _cp_values()). Der
# Klartext zu charge_status wird gesondert behandelt: der Modbus-Client
# hängt "_text" an den VOLLEN (bereits suffigierten) Registerschlüssel an
# (siehe app.modbus.client.StationClient.read_all), das Suffix steht also
# VOR "_text", nicht dahinter.
_KNOWN_VALUE_KEYS = (
    "current_l1", "current_l2", "current_l3",
    "active_power", "energy_total", "charge_status",
)

# Davon werden diese Schlüssel als Messwert-Historie persistiert
_PERSIST_KEYS = ("current_l1", "current_l2", "current_l3", "active_power", "energy_total")

# Statuswörter zur Erkennung eines ladebereiten/ladenden Fahrzeugs
_IDLE_WORDS = ("verfügbar", "available", "frei", "kein", "idle", "getrennt", "disconnect", "none")
_ERROR_WORDS = ("fehler", "error", "fault", "störung")


def _cp_values(cp: ChargePointSpec, raw: dict) -> dict:
    """Extrahiert die zu einem Ladepunkt gehörenden Werte aus dem
    Roh-Ergebnis seiner Station (Connector-Suffix entfernt)."""
    out = {}
    for base in _KNOWN_VALUE_KEYS:
        key = base + cp.connector_suffix
        if key in raw:
            out[base] = raw[key]
    text_key = "charge_status" + cp.connector_suffix + "_text"
    if text_key in raw:
        out["charge_status_text"] = raw[text_key]
    return out


def _wants_charge(values: dict) -> bool:
    """Heuristik: Will/kann der Ladepunkt laden? (Fahrzeug verbunden)."""
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


def _root_board(boards_data: list[dict]) -> dict:
    """Findet die Wurzel (Hauptverteilung, parent_board_id is None) im Baum."""
    return next(b for b in boards_data if b["parent_board_id"] is None)


def _build_board_tree(boards_data: list[dict], alloc_by_board: dict[int, list[AllocStation]]) -> BoardNode:
    """Baut den BoardNode-Baum aus den flachen DB-Zeilen und den je Verteiler
    direkt angeschlossenen (ladebereiten) Ladepunkten."""
    by_id = {b["id"]: b for b in boards_data}
    children_of: dict[int | None, list[dict]] = {}
    for b in boards_data:
        children_of.setdefault(b["parent_board_id"], []).append(b)

    def build(board_id: int) -> BoardNode:
        b = by_id[board_id]
        child_nodes = tuple(build(c["id"]) for c in children_of.get(board_id, []))
        return BoardNode(
            id=b["id"],
            fuse_a=b["fuse_a"],
            priority=b["priority"],
            strategy=b.get("strategy"),
            children=child_nodes,
            stations=tuple(alloc_by_board.get(board_id, [])),
        )

    return build(_root_board(boards_data)["id"])


class LoadManagerService:
    """Verwaltet Clients, Zustand und den periodischen Regelzyklus."""

    def __init__(self):
        self._clients: dict[int, StationClient] = {}
        self._smoother = SetpointSmoother()
        self._last_success: dict[int, float] = {}  # station_id -> monotonic (Verbindungsebene)
        self._last_written: dict[int, tuple[float, int | None]] = {}  # charge_point_id
        self._plug_order: dict[int, int] = {}  # charge_point_id
        self._plug_seq = 0
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._tz = ZoneInfo(settings.timezone)
        # Momentaufnahme für die API (thread-/task-sicher genug via Zuweisung)
        self.snapshot: dict = {
            "charge_points": {},
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

    def _load_config(self):
        """Lädt Konfiguration, Stationen, Ladepunkte, Zeitpläne, Zähler und
        den Verteilungsbaum (blockierend)."""
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

            stations = session.query(ChargingStation).all()
            specs = [StationSpec.from_station(s) for s in stations]

            charge_points = (
                session.query(ChargePoint)
                .filter(ChargePoint.enabled.is_(True))
                .options(joinedload(ChargePoint.schedules))
                .all()
            )
            cp_specs = [ChargePointSpec.from_charge_point(cp) for cp in charge_points]
            schedules_by_cp = {
                cp.id: [
                    ScheduleWindow(
                        weekdays_mask=sch.weekdays_mask,
                        start_time=sch.start_time,
                        end_time=sch.end_time,
                    )
                    for sch in cp.schedules
                ]
                for cp in charge_points
            }

            # Wurzel (Hauptverteilung) garantiert vorhanden; Default-Absicherung
            # = aktuelle Netzgrenze, damit Bestandsinstallationen ohne
            # Unterverteiler unverändert weiterlaufen.
            DistributionBoard.get_or_create_root(session, default_fuse_a=cfg.grid_limit_current_a)
            boards_data = [
                {
                    "id": b.id,
                    "parent_board_id": b.parent_board_id,
                    "fuse_a": b.incoming_fuse_a,
                    "priority": b.priority,
                    "strategy": b.strategy,
                }
                for b in session.query(DistributionBoard).all()
            ]

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
                    )
        return cfg_dict, specs, cp_specs, schedules_by_cp, meter_spec, boards_data

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
        cfg, specs, cp_specs, schedules_by_cp, meter, boards_data = await asyncio.to_thread(self._load_config)
        self._sync_clients(specs, meter)
        spec_by_id = {s.id: s for s in specs}

        # Hysterese-Parameter aktualisieren
        self._smoother.min_change_a = cfg["setpoint_min_change_a"]
        self._smoother.min_hold_s = cfg["setpoint_min_hold_s"]

        now_monotonic = time.monotonic()
        now_local = datetime.now(self._tz)

        # 1) Alle Stationen (Verbindungen) parallel pollen
        results = await asyncio.gather(*(self._poll_station(s) for s in specs))
        values_by_id: dict[int, dict] = {}
        station_error_by_id: dict[int, str | None] = {}
        for sid, values, error in results:
            values_by_id[sid] = values
            station_error_by_id[sid] = error
            if error is None:
                self._last_success[sid] = now_monotonic

        # 2) Online-/Offline-Status der STATIONEN (Verbindungsebene) anhand
        # Fail-Safe-Zeitfenster
        online_station_ids: set[int] = set()
        offline_station_ids: set[int] = set()
        for spec in specs:
            last = self._last_success.get(spec.id)
            is_online = station_error_by_id.get(spec.id) is None and bool(values_by_id.get(spec.id))
            within = last is not None and (now_monotonic - last) <= cfg["fail_safe_after_s"]
            if is_online:
                online_station_ids.add(spec.id)
            elif not within:
                offline_station_ids.add(spec.id)

        # Ladepunkte, deren Station nicht erreichbar ist -> Fail-Safe-Reserve
        offline_cps = []
        for cp in cp_specs:
            if cp.station_id in offline_station_ids:
                offline_cps.append(cp)
                self._smoother.reset(cp.id)

        # 3) Effektive Grenze bestimmen (§14a hat Vorrang, danach die
        # Absicherung der Hauptverteilung – beides wirkt zusätzlich zur
        # bestehenden Netzgrenze, nicht anstelle davon)
        root_board = _root_board(boards_data)
        effective_limit = cfg["grid_limit_current_a"]
        en14a_active = bool(cfg["en14a_enabled"] and cfg["en14a_active"])
        if en14a_active:
            effective_limit = min(effective_limit, cfg["en14a_limit_current_a"])
        effective_limit = min(effective_limit, root_board["fuse_a"])
        capacity = {p: effective_limit for p in PHASES}

        # 4) Dynamisches Lastmanagement: Grundlast abziehen, PV-Überschuss
        # ermitteln (für den nachrangigen PV-Only-Verteilungslauf, Schritt 7)
        meter_ok = True
        surplus = {p: 0.0 for p in PHASES}
        if cfg["management_mode"] == ManagementMode.DYNAMIC and meter is not None:
            capacity, surplus, meter_ok = await self._apply_dynamic_meter(
                meter, capacity, values_by_id, cp_specs, online_station_ids
            )

        # 5) Reserven für unerreichbare Ladepunkte abziehen (konservativ)
        capacity = safety.subtract_reservations(capacity, offline_cps)
        capacity = safety.clamp_capacity(capacity)

        # 6) Ladebereite Online-Ladepunkte ermitteln (Zeitplan-Sperre, will-
        # laden-Heuristik, FIFO-Reihenfolge pflegen), getrennt nach
        # Grid-Vorrang und PV-Only, gruppiert nach Verteiler.
        grid_alloc_by_board: dict[int, list[AllocStation]] = {}
        pv_alloc_by_board: dict[int, list[AllocStation]] = {}
        active_cps: list[ChargePointSpec] = []
        for cp in cp_specs:
            if cp.station_id not in online_station_ids:
                continue
            values = _cp_values(cp, values_by_id.get(cp.station_id, {}))
            if not _wants_charge(values):
                self._plug_order.pop(cp.id, None)
                continue
            if is_blocked(schedules_by_cp.get(cp.id, []), now_local):
                self._plug_order.pop(cp.id, None)
                continue
            if cp.id not in self._plug_order:
                self._plug_seq += 1
                self._plug_order[cp.id] = self._plug_seq
            active_cps.append(cp)
            alloc_station = AllocStation(
                id=cp.id,
                phases=cp.phases,
                min_current_a=cp.min_current_a,
                max_current_a=cp.max_current_a,
                priority=cp.priority,
                order=self._plug_order[cp.id],
            )
            board_id = cp.distribution_board_id or root_board["id"]
            group = pv_alloc_by_board if cp.pv_surplus_only else grid_alloc_by_board
            group.setdefault(board_id, []).append(alloc_station)

        # 7) Verteilung berechnen: Lauf 1 (Grid-Vorrang, harte Grenzgarantie
        # auf JEDER Ebene des Verteilungsbaums) + Lauf 2 (PV-Only, nachrangig,
        # begrenzt durch PV-Überschuss UND die je Verteiler nach Lauf 1 noch
        # freie Absicherung).
        grid_tree = _build_board_tree(boards_data, grid_alloc_by_board)
        targets = allocate_tree(grid_tree, capacity, cfg["distribution_strategy"])
        if pv_alloc_by_board:
            pv_tree = remaining_capacity_tree(grid_tree, targets, pv_alloc_by_board)
            pv_capacity = {p: min(surplus[p], pv_tree.fuse_a) for p in PHASES}
            pv_targets = allocate_tree(pv_tree, pv_capacity, cfg["distribution_strategy"])
            targets.update(pv_targets)

        alloc_stations = [
            s
            for stations in list(grid_alloc_by_board.values()) + list(pv_alloc_by_board.values())
            for s in stations
        ]

        # 8) Sollwerte glätten und schreiben
        await self._write_setpoints(active_cps, spec_by_id, targets)

        # 9) Messwerte persistieren + Momentaufnahme aktualisieren
        await asyncio.to_thread(self._persist, cp_specs, values_by_id, online_station_ids)
        self._update_snapshot(
            cp_specs, values_by_id, station_error_by_id, online_station_ids, targets,
            capacity, effective_limit, en14a_active, alloc_stations, meter_ok
        )
        return max(0.5, cfg["poll_interval_s"])

    async def _apply_dynamic_meter(self, meter, capacity, values_by_id, cp_specs, online_station_ids):
        """Berechnet die verfügbare Kapazität abzüglich der Haus-Grundlast
        sowie den PV-Überschuss (Einspeisung) je Phase.

        Der Zähler liefert vorzeichenbehaftete Ströme: positiv = Netzbezug,
        negativ = Einspeisung. Ein negativer Nettowert (nach Abzug der
        eigenen Wallbox-Last) ist verfügbarer PV-Überschuss.
        """
        try:
            meter_vals = await asyncio.wait_for(
                self._clients[meter.id].read_all(), timeout=settings.modbus_timeout_s + 1
            )
        except (ModbusError, asyncio.TimeoutError, OSError) as exc:
            # Fail-Safe: Zähler nicht lesbar -> konservativ nur Minimalbetrieb,
            # kein PV-Überschuss annehmen.
            log.warning("Netzanschlusszähler nicht lesbar (%s) – konservative Begrenzung", exc)
            self.snapshot["meter_values"] = {}
            return {p: 0.0 for p in PHASES}, {p: 0.0 for p in PHASES}, False

        self.snapshot["meter_values"] = meter_vals
        capacity_result = {}
        surplus_result = {}
        for i, phase in enumerate(PHASES, start=1):
            total = float(meter_vals.get(f"current_l{i}", meter_vals.get("current", 0.0)) or 0.0)
            # Aktuelle Wallbox-Last aller online Ladepunkte auf dieser Phase
            wb = 0.0
            for cp in cp_specs:
                if cp.station_id in online_station_ids and phase in cp.phases:
                    raw = values_by_id.get(cp.station_id, {})
                    wb += float(raw.get(f"current_l{i}{cp.connector_suffix}", 0.0) or 0.0)
            net = total - wb  # > 0: Netzbezug (Grundlast), < 0: Einspeisung
            capacity_result[phase] = max(0.0, capacity[phase] - max(0.0, net))
            surplus_result[phase] = max(0.0, -net)
        return capacity_result, surplus_result, True

    async def _write_setpoints(
        self,
        active_cps: list[ChargePointSpec],
        spec_by_id: dict[int, StationSpec],
        targets: dict[int, float],
    ) -> None:
        """Schreibt Sollstrom + enable je Ladepunkt, geglättet und nur bei
        Änderung. Registerauflösung berücksichtigt den Connector-Suffix."""
        for cp in active_cps:
            client = self._clients.get(cp.station_id)
            station_spec = spec_by_id.get(cp.station_id)
            if client is None or station_spec is None:
                continue
            target = targets.get(cp.id, 0.0)
            smoothed = self._smoother.desired(cp.id, target)

            set_spec = cp.register(station_spec.profile, "set_current")
            enable_spec = cp.register(station_spec.profile, "enable")
            enable_val = 0 if smoothed <= 0.0 else 1

            last = self._last_written.get(cp.id)
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
                        "Ladepunkt %s: kein enable-Register – Pausieren nur via Minimalstrom möglich",
                        cp.name,
                    )
                self._last_written[cp.id] = new_state
                reason = "pausiert (unter Minimalstrom)" if smoothed <= 0 else f"Zuteilung {smoothed:.1f} A"
                log.info("Sollwert Ladepunkt %s -> %.1f A (%s)", cp.name, smoothed, reason)
            except (ModbusError, asyncio.TimeoutError, OSError) as exc:
                log.warning("Ladepunkt %s: Sollwert-Schreiben fehlgeschlagen: %s", cp.name, exc)

    # --- Persistenz & Momentaufnahme --------------------------------------

    def _persist(
        self,
        cp_specs: list[ChargePointSpec],
        values_by_id: dict[int, dict],
        online_station_ids: set[int],
    ) -> None:
        """Persistiert ausgewählte Messwerte der online erreichbaren Ladepunkte."""
        ts = datetime.now(self._tz)
        rows = []
        for cp in cp_specs:
            if cp.station_id not in online_station_ids:
                continue
            values = _cp_values(cp, values_by_id.get(cp.station_id, {}))
            for key in _PERSIST_KEYS:
                val = values.get(key)
                if isinstance(val, (int, float)):
                    rows.append(Measurement(charge_point_id=cp.id, timestamp=ts, key=key, value=float(val)))
        if not rows:
            return
        try:
            with session_scope() as session:
                session.add_all(rows)
        except Exception:  # pragma: no cover
            log.exception("Messwerte konnten nicht gespeichert werden")

    def _update_snapshot(
        self, cp_specs, values_by_id, station_error_by_id, online_station_ids, targets,
        capacity, effective_limit, en14a_active, alloc_stations, meter_ok,
    ):
        cp_snap = {}
        for cp in cp_specs:
            online = cp.station_id in online_station_ids
            values = _cp_values(cp, values_by_id.get(cp.station_id, {})) if online else {}
            cp_snap[cp.id] = {
                "charge_point_id": cp.id,
                "name": cp.name,
                "online": online,
                "values": values,
                "setpoint_a": targets.get(cp.id),
                "error": None if online else station_error_by_id.get(cp.station_id),
            }
        load = phase_totals(targets, alloc_stations)
        self.snapshot = {
            **self.snapshot,
            "charge_points": cp_snap,
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
