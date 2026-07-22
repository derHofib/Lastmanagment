"""Integrationstest gegen einen echten pymodbus-TCP-Server.

Verifiziert das Zusammenspiel aus generischem Profil, Client, Kodierung und
Skalierung end-to-end: Ein Server stellt ein Beispielprofil bereit, der Client
liest alle Register und schreibt einen Sollwert zurück.
"""

import asyncio
import socket
import struct

import pytest

from app.models.base import DataType, RegisterRole
from app.modbus.client import StationClient
from app.modbus.runtime import ProfileSpec, RegisterSpec, StationSpec

pymodbus_server = pytest.importorskip("pymodbus.server")
from pymodbus.datastore import (  # noqa: E402
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import StartAsyncTcpServer  # noqa: E402

# pymodbus benannte ModbusSlaveContext (<=3.13) in ModbusDeviceContext (>=3.14) um
try:  # pragma: no cover - versionsabhängig
    from pymodbus.datastore import ModbusSlaveContext as _DeviceContext
except ImportError:  # pragma: no cover
    from pymodbus.datastore import ModbusDeviceContext as _DeviceContext


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _build_context() -> ModbusServerContext:
    """Datastore mit bekannten Werten.

    Input-Register (fc=4): current_l1 = 15.5 A als float32 (big/big) ab Adresse 100
    Holding-Register (fc=3): charge_status = 2 ab Adresse 200
    Holding-Register (fc=6): set_current ab Adresse 300 (Startwert 6)
    """
    # float32 15.5 -> zwei Register big/big
    hi, lo = struct.unpack(">HH", struct.pack(">f", 15.5))

    # Input-Register: Adresse 100/101 belegt
    input_values = [0] * 400
    input_values[100] = hi
    input_values[101] = lo

    holding_values = [0] * 400
    holding_values[200] = 2  # charge_status
    holding_values[300] = 6  # set_current

    # Startadresse 1: values[0] wird auf Protokolladresse 0 abgebildet, d. h.
    # values[N] liegt auf Adresse N (pymodbus zieht intern 1 ab).
    slave = _DeviceContext(
        di=ModbusSequentialDataBlock(1, [0] * 400),
        co=ModbusSequentialDataBlock(1, [0] * 400),
        hr=ModbusSequentialDataBlock(1, holding_values),
        ir=ModbusSequentialDataBlock(1, input_values),
    )
    # Parametername wechselte von 'slaves' zu 'devices'
    try:
        return ModbusServerContext(devices=slave, single=True)
    except TypeError:  # pragma: no cover - ältere pymodbus-Versionen
        return ModbusServerContext(slaves=slave, single=True)


def _example_station(port: int) -> StationSpec:
    regs = {
        "current_l1": RegisterSpec("current_l1", RegisterRole.READ, 100, 4,
                                   DataType.FLOAT32, "big", "big", unit="A"),
        "charge_status": RegisterSpec("charge_status", RegisterRole.READ, 200, 3,
                                      DataType.UINT16, "big", "big",
                                      enum_map={"0": "Verfügbar", "2": "Lädt"}),
        "set_current": RegisterSpec("set_current", RegisterRole.WRITE, 300, 6,
                                    DataType.UINT16, "big", "big", unit="A",
                                    writable_min=6, writable_max=32),
    }
    profile = ProfileSpec(name="Test", registers=regs)
    return StationSpec(
        id=1, name="Testbox", ip_address="127.0.0.1", tcp_port=port, unit_id=1,
        profile=profile, phases=("L1", "L2", "L3"), priority=0,
        max_current_a=32, min_current_a=6, safe_state="block",
    )


@pytest.mark.asyncio
async def test_read_write_against_real_server():
    port = _free_port()
    context = _build_context()

    server_task = asyncio.create_task(
        StartAsyncTcpServer(context=context, address=("127.0.0.1", port))
    )
    await asyncio.sleep(0.4)  # Server hochfahren lassen

    try:
        station = _example_station(port)
        client = StationClient(station, timeout_s=2.0, retries=1)

        # Alle read-Register auslesen
        values = await client.read_all()
        assert values["current_l1"] == pytest.approx(15.5, rel=1e-4)
        assert values["charge_status"] == 2
        assert values["charge_status_text"] == "Lädt"

        # Sollwert schreiben (innerhalb der Grenzen)
        set_spec = station.profile.registers["set_current"]
        # Rücklese-Register: dasselbe Holding-Register, aber als Leseregister (fc=3)
        readback_spec = RegisterSpec("set_current_rb", RegisterRole.READ, 300, 3,
                                     DataType.UINT16, "big", "big")
        await client.write_register(set_spec, 16)
        assert await client.read_register(readback_spec) == 16

        # Schreibwert über der Grenze wird geklemmt (max 32)
        await client.write_register(set_spec, 999)
        assert await client.read_register(readback_spec) == 32

        # Schreibwert unter der Grenze wird auf Minimum (6) geklemmt
        await client.write_register(set_spec, 1)
        assert await client.read_register(readback_spec) == 6

        await client.close()
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass
