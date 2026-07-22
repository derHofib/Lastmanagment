"""Modbus-TCP-Wallbox-Simulator zum Verifizieren von Geräteprofilen.

Startet einen Modbus-TCP-Server, der die Register des Beispielprofils aus dem
README bereitstellt. Damit lassen sich Profile und der Regelzyklus ohne echte
Hardware testen.

Aufruf:
    python -m tools.simulator --port 5020 --current 16

Register (passend zum Beispielprofil "Beispiel-Wallbox 22kW"):
    Input   (fc=4) 100/101  current_l1  float32 [A]
    Input   (fc=4) 102/103  current_l2  float32 [A]
    Input   (fc=4) 104/105  current_l3  float32 [A]
    Input   (fc=4) 120/121  active_power uint32 [W]
    Input   (fc=4) 130/131  energy_total uint32 [0,1 kWh]
    Holding (fc=3) 200      charge_status uint16 (0..3)
    Holding (fc=6) 300      set_current  uint16 [A]
    Holding (fc=6) 301      enable       uint16 (0/1)
"""

from __future__ import annotations

import argparse
import asyncio
import struct

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext
from pymodbus.server import StartAsyncTcpServer

try:  # pymodbus >=3.14
    from pymodbus.datastore import ModbusDeviceContext as _DeviceContext
except ImportError:  # pymodbus <=3.13
    from pymodbus.datastore import ModbusSlaveContext as _DeviceContext


def _f32(value: float) -> tuple[int, int]:
    return struct.unpack(">HH", struct.pack(">f", value))


def _u32(value: int) -> tuple[int, int]:
    return struct.unpack(">HH", struct.pack(">I", value & 0xFFFFFFFF))


def build_context(current: float = 16.0, power: int = 11000, energy_dwh: int = 12345,
                  status: int = 2) -> ModbusServerContext:
    ir = [0] * 400  # Input-Register
    hr = [0] * 400  # Holding-Register

    for base, phase in ((100, current), (102, current), (104, current)):
        ir[base], ir[base + 1] = _f32(phase)
    ir[120], ir[121] = _u32(power)
    ir[130], ir[131] = _u32(energy_dwh)  # * 0.1 -> kWh

    hr[200] = status
    hr[300] = int(current)
    hr[301] = 1

    # Startadresse 1 -> values[N] liegt auf Modbus-Adresse N (pymodbus-Konvention)
    device = _DeviceContext(
        hr=ModbusSequentialDataBlock(1, hr),
        ir=ModbusSequentialDataBlock(1, ir),
        di=ModbusSequentialDataBlock(1, [0] * 400),
        co=ModbusSequentialDataBlock(1, [0] * 400),
    )
    try:
        return ModbusServerContext(devices=device, single=True)
    except TypeError:  # ältere pymodbus-Versionen
        return ModbusServerContext(slaves=device, single=True)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Modbus-TCP-Wallbox-Simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--current", type=float, default=16.0, help="Simulierter Ladestrom (A)")
    parser.add_argument("--power", type=int, default=11000, help="Simulierte Leistung (W)")
    parser.add_argument("--status", type=int, default=2, help="charge_status (0..3)")
    args = parser.parse_args()

    context = build_context(current=args.current, power=args.power, status=args.status)
    print(f"Wallbox-Simulator läuft auf {args.host}:{args.port} "
          f"(Strom={args.current} A, Status={args.status})")
    await StartAsyncTcpServer(context=context, address=(args.host, args.port))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSimulator beendet.")
