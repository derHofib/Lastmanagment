"""Zeitgeraffte Demo-Szenario-Simulation für Voltibus.

Bildet einen realistischen Tagesablauf ab statt konstant voller Leistung:
Fahrzeuge stecken sich zufällig an/ab, laden mit einer Ladekurve (Rampe,
Plateau, Taper), ein Netzanschlusszähler hat einen Tagesgang (PV-Überschuss
mittags, Bezug abends), ein §14a-Steuersignal wird periodisch geschaltet –
und das Skript richtet die laufende Anwendung dafür automatisch per
REST-API ein (Geräteprofile, Doppel-Wallbox-Station, Verteiler, Ladepunkte,
Konfiguration).

Zeitraffer: 1 reale Minute entspricht 1 simulierter Stunde (Faktor 60 = eine
simulierte Minute pro realer Sekunde, per --speed änderbar).

Wichtige Ausnahme: Der **Zeitplan** (ChargeSchedule) der echten Anwendung
wertet die tatsächliche Systemzeit aus (so soll es im Produktivbetrieb
sein – ein Ladepunkt darf sich nicht von einer simulierten Uhr täuschen
lassen). Das Sperrfenster des Demo-Ladepunkts wird deshalb relativ zur
tatsächlichen Startzeit dieses Skripts gelegt, nicht zur simulierten Zeit
(Konsolenausgabe beim Start zeigt das reale Zeitfenster).

Technischer Hinweis: pymodbus 3.14 hat die klassische
``ModbusSequentialDataBlock``/``ModbusDeviceContext``-API als Fassade vor
einem neuen, internen "SimData/SimDevice"-Simulator-Kern deprecatet, der
keine unterstützte Möglichkeit mehr bietet, Registerwerte eines LAUFENDEN
Servers aus Python heraus zu ändern (empirisch geprüft). Für dieses Skript
wird deshalb ein winziger, selbst geschriebener Modbus-TCP-Server verwendet
(nur Function Codes 3/4/6/16 – alles, was die Anwendung tatsächlich nutzt),
dessen Register schlicht Python-Listen sind. Das ist unabhängig von
pymodbus-Serverinterna und mit einem echten ``pymodbus``-Client getestet.

Aufruf (Anwendung muss bereits laufen):
    uvicorn app.main:app --port 8000 &
    python -m tools.scenario
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import math
import random
import struct
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timedelta

import httpx

from app.modbus.codec import encode_value
from app.models.base import DataType

log = logging.getLogger("scenario")


# ============================================================================
# Minimaler Modbus-TCP-Server (siehe Modulkommentar für den "Warum")
# ============================================================================

class MiniModbusServer:
    """Bedient fc 3 (Holding lesen), 4 (Input lesen), 6/16 (schreiben).

    ``hr``/``ir`` sind gewöhnliche Python-Listen und dürfen jederzeit direkt
    aus dem simulierenden Code gelesen/geschrieben werden – das ist der
    ganze Sinn dieser Klasse.
    """

    def __init__(self, size: int = 2000) -> None:
        self.hr: list[int] = [0] * size
        self.ir: list[int] = [0] * size
        self._server: asyncio.AbstractServer | None = None

    async def start(self, host: str, port: int) -> None:
        self._server = await asyncio.start_server(self._handle, host, port)

    def close(self) -> None:
        if self._server is not None:
            self._server.close()

    async def wait_closed(self) -> None:
        if self._server is not None:
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                header = await reader.readexactly(7)
                trans_id, _proto_id, length, unit_id = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(length - 1)
                body = self._handle_pdu(pdu)
                resp = struct.pack(">HHHB", trans_id, 0, len(body) + 1, unit_id) + body
                writer.write(resp)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    def _handle_pdu(self, pdu: bytes) -> bytes:
        fc = pdu[0]
        try:
            if fc in (3, 4):
                addr, qty = struct.unpack(">HH", pdu[1:5])
                block = self.hr if fc == 3 else self.ir
                values = block[addr : addr + qty]
                return bytes([fc, qty * 2]) + b"".join(struct.pack(">H", v & 0xFFFF) for v in values)
            if fc == 6:
                addr, value = struct.unpack(">HH", pdu[1:5])
                self.hr[addr] = value
                return pdu
            if fc == 16:
                addr, qty, byte_count = struct.unpack(">HHB", pdu[1:6])
                values = struct.unpack(f">{qty}H", pdu[6 : 6 + byte_count])
                for i, v in enumerate(values):
                    self.hr[addr + i] = v
                return struct.pack(">HH", addr, qty)
        except (struct.error, IndexError):
            pass
        return bytes([fc | 0x80, 0x02])  # Illegal Data Address (Fallback)

    def write_ir(self, address: int, value: float, data_type: DataType, scale: float = 1.0) -> None:
        for i, reg in enumerate(encode_value(value, data_type, scale=scale)):
            self.ir[address + i] = reg

    def write_hr_raw(self, address: int, value: int) -> None:
        self.hr[address] = value

    def read_hr_raw(self, address: int) -> int:
        return self.hr[address]


# ============================================================================
# Register-Layout (Adressen je Connector; identisch zum README-Beispielprofil,
# um Connector "_2" für die Doppel-Wallbox erweitert)
# ============================================================================

def _connector_addresses(suffix: str) -> dict[str, int]:
    base_ir, base_hr = (100, 200) if suffix in ("", "_1") else (150, 210)
    return {
        "current_l1": base_ir, "current_l2": base_ir + 2, "current_l3": base_ir + 4,
        "active_power": base_ir + 20, "energy_total": base_ir + 30,
        "charge_status": base_hr, "set_current": base_hr + 100, "enable": base_hr + 101,
    }


METER_ADDR = {"current_l1": 100, "current_l2": 102, "current_l3": 104}


def _profile_registers(suffix: str = "") -> list[dict]:
    a = _connector_addresses(suffix)
    return [
        {"key": f"current_l1{suffix}", "role": "read", "function_code": 4,
         "register_address": a["current_l1"], "data_type": "float32", "scale": 1, "unit": "A"},
        {"key": f"current_l2{suffix}", "role": "read", "function_code": 4,
         "register_address": a["current_l2"], "data_type": "float32", "scale": 1, "unit": "A"},
        {"key": f"current_l3{suffix}", "role": "read", "function_code": 4,
         "register_address": a["current_l3"], "data_type": "float32", "scale": 1, "unit": "A"},
        {"key": f"active_power{suffix}", "role": "read", "function_code": 4,
         "register_address": a["active_power"], "data_type": "uint32", "scale": 1, "unit": "W"},
        {"key": f"energy_total{suffix}", "role": "read", "function_code": 4,
         "register_address": a["energy_total"], "data_type": "uint32", "scale": 0.1, "unit": "kWh"},
        {"key": f"charge_status{suffix}", "role": "read", "function_code": 3,
         "register_address": a["charge_status"], "data_type": "uint16",
         "enum_map": {"0": "Verfügbar", "1": "Fahrzeug verbunden", "2": "Lädt", "3": "Fehler"}},
        {"key": f"set_current{suffix}", "role": "write", "function_code": 6,
         "register_address": a["set_current"], "data_type": "uint16", "unit": "A",
         "writable_min": 6, "writable_max": 32},
        {"key": f"enable{suffix}", "role": "write", "function_code": 6,
         "register_address": a["enable"], "data_type": "uint16", "writable_min": 0, "writable_max": 1},
    ]


def _meter_profile_registers() -> list[dict]:
    return [
        {"key": f"current_l{i}", "role": "read", "function_code": 4,
         "register_address": METER_ADDR[f"current_l{i}"], "data_type": "float32", "scale": 1, "unit": "A"}
        for i in (1, 2, 3)
    ]


# ============================================================================
# Zeitraffer-Uhr
# ============================================================================

class SimClock:
    def __init__(self, speed: float, start_hour: int) -> None:
        self._t0 = asyncio.get_event_loop().time()
        self._speed = speed
        today = datetime.now().date()
        self._start = datetime.combine(today, dt_time(hour=start_hour))

    def now(self) -> datetime:
        elapsed_real = asyncio.get_event_loop().time() - self._t0
        return self._start + timedelta(seconds=elapsed_real * self._speed)


STATUS_AVAILABLE, STATUS_CONNECTED, STATUS_CHARGING = 0, 1, 2


# ============================================================================
# Fahrzeug-Sitzung je Ladepunkt: An-/Abstecken, Rampe, Plateau, Taper
# ============================================================================

@dataclass
class EvSession:
    name: str
    rng: random.Random
    state: str = "empty"  # empty -> connected -> charging -> done -> empty
    next_transition: datetime | None = None
    session_target_kwh: float = 0.0
    session_energy_kwh: float = 0.0
    current_a: float = 0.0

    def tick(self, sim_now: datetime, sim_minutes: float, setpoint_a: float, enabled: bool) -> tuple[float, int]:
        # Zufälliges vorzeitiges Abstecken ("ein Auto steckt sich mal ab") –
        # kleine Wahrscheinlichkeit je simulierter Minute, gedeckelt, damit
        # sie auch bei hohem --speed nicht jede Ladung sofort abbricht.
        if self.state in ("connected", "charging") and self.rng.random() < min(0.15, 0.012 * sim_minutes):
            log.info("%s: Fahrzeug wurde vorzeitig abgesteckt", self.name)
            self.state = "empty"
            self.next_transition = sim_now + timedelta(minutes=self.rng.uniform(10, 90))
            self.current_a = 0.0
            return 0.0, STATUS_AVAILABLE

        if self.state == "empty":
            if self.next_transition is None:
                self.next_transition = sim_now + timedelta(minutes=self.rng.uniform(5, 40))
            if sim_now >= self.next_transition:
                self.state = "connected"
                self.next_transition = sim_now + timedelta(minutes=self.rng.uniform(1, 5))
                self.session_target_kwh = self.rng.uniform(8, 45)
                self.session_energy_kwh = 0.0
                log.info("%s: Fahrzeug angesteckt (Ziel ca. %.0f kWh)", self.name, self.session_target_kwh)
            self.current_a = 0.0
            return 0.0, STATUS_AVAILABLE

        if self.state == "connected":
            if sim_now >= self.next_transition:
                self.state = "charging"
                log.info("%s: Ladevorgang gestartet", self.name)
            return 0.0, STATUS_CONNECTED

        if self.state == "charging":
            if not enabled or setpoint_a <= 0:
                self.current_a = max(0.0, self.current_a - 6.0)  # klingt ab statt Sprung auf 0
                return self.current_a, STATUS_CONNECTED

            progress = min(1.0, self.session_energy_kwh / max(1.0, self.session_target_kwh))
            # Ladekurve: volles Plateau bis 80 %, danach Taper Richtung Ende
            taper = 1.0 if progress <= 0.8 else max(0.15, 1.0 - (progress - 0.8) / 0.2 * 0.85)
            noise = 0.94 + self.rng.random() * 0.06
            target = setpoint_a * taper * noise
            # sanftes Ein-/Ausregeln statt Sprüngen (reale Wallboxen regeln auch nicht sofort)
            step = max(-4.0, min(4.0, target - self.current_a))
            self.current_a = max(0.0, self.current_a + step)

            energy_kwh = self.current_a * 230 * 3 / 1000 * (sim_minutes / 60)
            self.session_energy_kwh += energy_kwh

            if self.session_energy_kwh >= self.session_target_kwh:
                self.state = "done"
                self.next_transition = sim_now + timedelta(minutes=self.rng.uniform(5, 120))
                log.info("%s: Ladevorgang abgeschlossen (%.1f kWh)", self.name, self.session_energy_kwh)
                self.current_a = 0.0
                return 0.0, STATUS_CONNECTED
            return self.current_a, STATUS_CHARGING

        if self.state == "done":
            if sim_now >= self.next_transition:
                self.state = "empty"
                self.next_transition = sim_now + timedelta(minutes=self.rng.uniform(1, 10))
                log.info("%s: Fahrzeug abgesteckt", self.name)
            return 0.0, STATUS_CONNECTED

        return 0.0, STATUS_AVAILABLE  # pragma: no cover - unerreichbar


@dataclass
class Connector:
    session: EvSession
    server: MiniModbusServer
    addr: dict[str, int]
    energy_total_kwh: float = field(default=35.0)

    def tick(self, sim_now: datetime, sim_minutes: float) -> float:
        raw_setpoint = self.server.read_hr_raw(self.addr["set_current"])
        raw_enable = self.server.read_hr_raw(self.addr["enable"])
        current_a, status = self.session.tick(sim_now, sim_minutes, float(raw_setpoint), bool(raw_enable))

        power_w = current_a * 230 * 3
        if current_a > 0:
            self.energy_total_kwh += current_a * 230 * 3 / 1000 * (sim_minutes / 60)

        self.server.write_ir(self.addr["current_l1"], current_a, DataType.FLOAT32)
        self.server.write_ir(self.addr["current_l2"], current_a, DataType.FLOAT32)
        self.server.write_ir(self.addr["current_l3"], current_a, DataType.FLOAT32)
        self.server.write_ir(self.addr["active_power"], power_w, DataType.UINT32)
        self.server.write_ir(self.addr["energy_total"], self.energy_total_kwh, DataType.UINT32, scale=0.1)
        self.server.write_hr_raw(self.addr["charge_status"], status)
        return current_a


# ============================================================================
# Netzanschlusszähler: Tagesgang (PV-Überschuss mittags, Bezug abends/nachts)
# ============================================================================

class MeterSim:
    def __init__(self, server: MiniModbusServer, rng: random.Random) -> None:
        self.server = server
        self.rng = rng
        self.base_load_a = 3.0

    def tick(self, sim_now: datetime, total_wallbox_current_a: float) -> None:
        hour = sim_now.hour + sim_now.minute / 60
        solar = max(0.0, math.sin((hour - 7) / 13 * math.pi)) if 7 <= hour <= 20 else 0.0
        solar_a = solar * 22.0  # bis zu ~5 kW/Phase Erzeugungsspitze
        house_a = self.base_load_a + self.rng.uniform(-0.5, 1.5)
        net_a = house_a + total_wallbox_current_a - solar_a
        for key in ("current_l1", "current_l2", "current_l3"):
            self.server.write_ir(METER_ADDR[key], net_a, DataType.FLOAT32)


# ============================================================================
# §14a-Steuersignal: periodisch per REST-API geschaltet (kein Register –
# im echten System ist en14a_active bewusst ein API-/Konfigurationswert)
# ============================================================================

class En14aToggler:
    def __init__(self, api: httpx.AsyncClient, peak_start: int = 17, peak_end: int = 19) -> None:
        self.api = api
        self.peak_start = peak_start
        self.peak_end = peak_end
        self.active: bool | None = None

    async def tick(self, sim_now: datetime) -> None:
        in_peak = self.peak_start <= sim_now.hour < self.peak_end
        if in_peak == self.active:
            return
        self.active = in_peak
        try:
            await self.api.put("/api/config", json={"en14a_active": in_peak})
            log.info(
                "§14a-Steuersignal: %s (simulierte Zeit %s)",
                "AKTIV – Leistung wird gedrosselt" if in_peak else "inaktiv",
                sim_now.strftime("%H:%M"),
            )
        except httpx.HTTPError as exc:
            log.warning("§14a-Signal konnte nicht gesetzt werden: %s", exc)


# ============================================================================
# Automatische Einrichtung der laufenden Anwendung per REST-API (idempotent)
# ============================================================================

async def _find_by_name(api: httpx.AsyncClient, path: str, name: str) -> dict | None:
    r = await api.get(path)
    r.raise_for_status()
    return next((x for x in r.json() if x["name"] == name), None)


async def configure_app(api: httpx.AsyncClient, dual_port: int, single_port: int, meter_port: int) -> dict:
    """Legt Profile/Verteiler/Stationen/Ladepunkte an, falls noch nicht
    vorhanden, und aktiviert dynamisches Lastmanagement + §14a. Gibt die
    IDs der angelegten Ladepunkte zurück (für die Zeitplan-Ausgabe)."""

    async def ensure_profile(name: str, registers: list[dict]) -> int:
        existing = await _find_by_name(api, "/api/profiles", name)
        if existing:
            return existing["id"]
        r = await api.post("/api/profiles", json={
            "name": name, "manufacturer": "Voltibus Szenario", "default_unit_id": 1,
            "byte_order": "big", "word_order": "big", "registers": registers,
        })
        r.raise_for_status()
        return r.json()["id"]

    single_profile_id = await ensure_profile("Szenario: Einzel-Wallbox", _profile_registers(""))
    dual_registers = _profile_registers("_1") + _profile_registers("_2")
    dual_profile_id = await ensure_profile("Szenario: Doppel-Wallbox", dual_registers)
    meter_profile_id = await ensure_profile("Szenario: Netzanschlusszähler", _meter_profile_registers())

    root_id = (await api.get("/api/boards")).json()[0]["id"]
    uv = await _find_by_name(api, "/api/boards", "UV Carport (Szenario)")
    if not uv:
        r = await api.post("/api/boards", json={
            "name": "UV Carport (Szenario)", "parent_board_id": root_id, "incoming_fuse_a": 25,
        })
        r.raise_for_status()
        uv = r.json()
    uv_id = uv["id"]

    async def ensure_station(name: str, port: int, profile_id: int) -> int:
        existing = await _find_by_name(api, "/api/stations", name)
        if existing:
            return existing["id"]
        r = await api.post("/api/stations", json={
            "name": name, "ip_address": "127.0.0.1", "tcp_port": port, "profile_id": profile_id,
        })
        r.raise_for_status()
        return r.json()["id"]

    dual_station_id = await ensure_station("Carport (Doppel-Wallbox, Szenario)", dual_port, dual_profile_id)
    single_station_id = await ensure_station("Stellplatz 3 (Szenario)", single_port, single_profile_id)

    async def ensure_charge_point(name: str, **fields) -> int:
        existing = await _find_by_name(api, "/api/charge-points", name)
        if existing:
            return existing["id"]
        r = await api.post("/api/charge-points", json={"name": name, **fields})
        r.raise_for_status()
        return r.json()["id"]

    now = datetime.now()
    sched_start = (now + timedelta(minutes=2)).time().replace(microsecond=0)
    sched_end = (now + timedelta(minutes=4)).time().replace(microsecond=0)

    left_id = await ensure_charge_point(
        "Carport links (Szenario)", station_id=dual_station_id, connector_suffix="_1",
        priority=5, max_current_a=32, min_current_a=6,
        distribution_board_id=uv_id, circuit_breaker_a=16,
        schedules=[{"weekdays_mask": 127, "start_time": sched_start.isoformat(), "end_time": sched_end.isoformat()}],
    )
    right_id = await ensure_charge_point(
        "Carport rechts (Szenario, PV)", station_id=dual_station_id, connector_suffix="_2",
        priority=1, max_current_a=32, min_current_a=6,
        distribution_board_id=uv_id, circuit_breaker_a=16, pv_surplus_only=True,
    )
    single_id = await ensure_charge_point(
        "Stellplatz 3 (Szenario)", station_id=single_station_id,
        priority=3, max_current_a=16, min_current_a=6,
    )

    r = await api.put("/api/config", json={
        "grid_limit_current_a": 50.0, "management_mode": "dynamic", "distribution_strategy": "priority",
        "poll_interval_s": 2.0, "dynamic_meter_enabled": True, "meter_profile_id": meter_profile_id,
        "meter_ip_address": "127.0.0.1", "meter_tcp_port": meter_port, "meter_unit_id": 1,
        "en14a_enabled": True,
    })
    r.raise_for_status()

    log.info("Anwendung eingerichtet: Doppel-Wallbox 'Carport', Einzel-Wallbox 'Stellplatz 3',")
    log.info("Unterverteilung 'UV Carport' (25 A), dynamisches Lastmanagement + §14a aktiv.")
    log.info(
        "Zeitplan-Sperre 'Carport links' gilt zur ECHTEN Uhrzeit %s–%s (nicht simuliert) – "
        "der Zeitplan der Anwendung wertet reale Systemzeit aus.",
        sched_start.strftime("%H:%M:%S"), sched_end.strftime("%H:%M:%S"),
    )
    return {"left": left_id, "right": right_id, "single": single_id}


# ============================================================================
# Orchestrierung
# ============================================================================

async def run(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)

    dual_server = MiniModbusServer()
    single_server = MiniModbusServer()
    meter_server = MiniModbusServer()
    await dual_server.start(args.host, args.dual_port)
    await single_server.start(args.host, args.single_port)
    await meter_server.start(args.host, args.meter_port)
    log.info(
        "Modbus-Simulatoren gestartet: Doppel-Wallbox=%s:%d, Einzel-Wallbox=%s:%d, Zähler=%s:%d",
        args.host, args.dual_port, args.host, args.single_port, args.host, args.meter_port,
    )

    async with httpx.AsyncClient(base_url=args.api, timeout=10.0) as api:
        for attempt in range(15):
            try:
                (await api.get("/healthz")).raise_for_status()
                break
            except httpx.HTTPError:
                if attempt == 0:
                    log.info("Warte auf laufende Anwendung unter %s ...", args.api)
                await asyncio.sleep(1.0)
        else:
            log.error("Anwendung unter %s nicht erreichbar. Bitte zuerst starten (uvicorn app.main:app).", args.api)
            return

        await configure_app(api, args.dual_port, args.single_port, args.meter_port)
        en14a = En14aToggler(api)

        clock = SimClock(speed=args.speed, start_hour=args.start_hour)
        connectors = [
            Connector(EvSession("Carport links", rng), dual_server, _connector_addresses("_1")),
            Connector(EvSession("Carport rechts", rng), dual_server, _connector_addresses("_2")),
            Connector(EvSession("Stellplatz 3", rng), single_server, _connector_addresses("")),
        ]
        meter = MeterSim(meter_server, rng)

        log.info(
            "Zeitraffer aktiv: 1 reale Minute = %.0f simulierte Minuten (Start bei %s Uhr simuliert).",
            args.speed, clock.now().strftime("%H:%M"),
        )
        log.info("Bereit. In der Weboberfläche unter http://127.0.0.1:8000/ zusehen (Strg+C beendet das Szenario).")

        sim_minutes_per_tick = args.speed / 60.0
        last_log = 0.0
        while True:
            sim_now = clock.now()
            total_current = sum(c.tick(sim_now, sim_minutes_per_tick) for c in connectors)
            meter.tick(sim_now, total_current)
            await en14a.tick(sim_now)

            loop_time = asyncio.get_event_loop().time()
            if loop_time - last_log >= 30:
                last_log = loop_time
                log.info("[sim %s] Gesamtstrom Ladepunkte: %.1f A", sim_now.strftime("%a %H:%M"), total_current)

            await asyncio.sleep(1.0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="Basis-URL der laufenden Anwendung")
    parser.add_argument("--host", default="127.0.0.1", help="Bind-Adresse der simulierten Wallboxen/des Zählers")
    parser.add_argument("--dual-port", type=int, default=5101, help="Port der simulierten Doppel-Wallbox")
    parser.add_argument("--single-port", type=int, default=5102, help="Port der simulierten Einzel-Wallbox")
    parser.add_argument("--meter-port", type=int, default=5103, help="Port des simulierten Netzanschlusszählers")
    parser.add_argument("--speed", type=float, default=60.0, help="Zeitraffer-Faktor (Standard: 60 = 1 min real = 1 h simuliert)")
    parser.add_argument("--start-hour", type=int, default=6, help="Simulierte Startuhrzeit (Stunde, 0-23)")
    parser.add_argument("--seed", type=int, default=None, help="Zufalls-Seed (für reproduzierbare Abläufe)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nSzenario beendet.")


if __name__ == "__main__":
    main()
