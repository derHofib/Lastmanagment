"""Asynchroner Modbus-TCP-Client pro Ladestation.

Eigenschaften:
    * Ein Client pro Station, isoliert – ein hängender Client blockiert nie
      den gesamten Regelzyklus (jede Station wird mit eigenem Timeout gepollt).
    * Automatischer (Wieder-)Verbindungsaufbau nach Abbruch.
    * Kodierung/Dekodierung über die generische :mod:`app.modbus.codec`.
    * Schreibwerte werden gegen ``writable_min``/``writable_max`` geklemmt –
      niemals wird ein Sollwert außerhalb der erlaubten Grenzen geschrieben.
"""

from __future__ import annotations

import asyncio
import inspect
import logging

from pymodbus.client import AsyncModbusTcpClient

from app.modbus import codec
from app.modbus.runtime import RegisterSpec, StationSpec

log = logging.getLogger("lastmanagement.modbus")


class ModbusError(Exception):
    """Fehler bei der Modbus-Kommunikation oder -Konfiguration."""


# pymodbus benannte den Slave-Parameter über die Versionen um
# (>=3.7: ``device_id``, älter: ``slave``). Wir ermitteln das einmalig.
_UNIT_KW = (
    "device_id"
    if "device_id" in inspect.signature(AsyncModbusTcpClient.read_holding_registers).parameters
    else "slave"
)


class StationClient:
    """Kapselt die Modbus-Verbindung zu genau einer Station."""

    def __init__(self, station: StationSpec, timeout_s: float = 3.0, retries: int = 2):
        self.station = station
        self.timeout_s = timeout_s
        self.retries = retries
        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Baut die Verbindung auf, falls noch nicht verbunden."""
        if self._client is not None and self._client.connected:
            return
        self._client = AsyncModbusTcpClient(
            host=self.station.ip_address,
            port=self.station.tcp_port,
            timeout=self.timeout_s,
            retries=self.retries,
        )
        await self._client.connect()
        if not self._client.connected:
            raise ModbusError(
                f"Verbindung zu {self.station.ip_address}:{self.station.tcp_port} fehlgeschlagen"
            )

    async def close(self) -> None:
        """Schließt die Verbindung sauber."""
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.connected

    async def _ensure(self) -> AsyncModbusTcpClient:
        await self.connect()
        assert self._client is not None
        return self._client

    async def read_register(self, spec: RegisterSpec) -> float | int | bool:
        """Liest ein einzelnes Register und liefert den physikalischen Wert."""
        count = codec.word_count(spec.data_type)
        kwargs = {"count": count, _UNIT_KW: self.station.unit_id}
        async with self._lock:
            client = await self._ensure()
            if spec.function_code == 3:
                result = await client.read_holding_registers(spec.register_address, **kwargs)
            elif spec.function_code == 4:
                result = await client.read_input_registers(spec.register_address, **kwargs)
            else:
                raise ModbusError(
                    f"Funktionscode {spec.function_code} ist kein Leseregister (Register '{spec.key}')"
                )
        if result.isError():
            raise ModbusError(f"Modbus-Fehler beim Lesen von '{spec.key}': {result}")
        return codec.decode_registers(
            result.registers,
            spec.data_type,
            byte_order=spec.byte_order,
            word_order=spec.word_order,
            scale=spec.scale,
            offset=spec.offset,
        )

    async def read_all(self) -> dict[str, object]:
        """Liest alle ``read``-Register des Profils einmalig aus.

        Fehler einzelner Register brechen den Vorgang nicht komplett ab; der
        betroffene Schlüssel fehlt dann im Ergebnis (konservativ).
        """
        values: dict[str, object] = {}
        for spec in self.station.profile.reads():
            try:
                raw = await self.read_register(spec)
                values[spec.key] = raw
                text = codec.apply_enum(raw, spec.enum_map)
                if text is not None:
                    values[f"{spec.key}_text"] = text
            except (ModbusError, asyncio.TimeoutError, OSError) as exc:
                log.warning("Station %s: Register '%s' nicht lesbar: %s",
                            self.station.name, spec.key, exc)
        return values

    def clamp_write_value(self, spec: RegisterSpec, value: float) -> float:
        """Begrenzt einen Schreibwert auf die erlaubten Registergrenzen."""
        if spec.writable_min is not None:
            value = max(spec.writable_min, value)
        if spec.writable_max is not None:
            value = min(spec.writable_max, value)
        return value

    async def write_register(self, spec: RegisterSpec, value: float) -> None:
        """Schreibt einen physikalischen Wert in ein Steuerregister.

        Der Wert wird vor dem Schreiben strikt auf die Registergrenzen geklemmt.
        """
        if spec.role.value != "write":
            raise ModbusError(f"Register '{spec.key}' ist kein Schreibregister")

        clamped = self.clamp_write_value(spec, value)
        if clamped != value:
            log.info("Station %s: Schreibwert %.2f auf %.2f geklemmt (Register '%s')",
                     self.station.name, value, clamped, spec.key)

        registers = codec.encode_value(
            clamped,
            spec.data_type,
            byte_order=spec.byte_order,
            word_order=spec.word_order,
            scale=spec.scale,
            offset=spec.offset,
        )
        kwargs = {_UNIT_KW: self.station.unit_id}
        async with self._lock:
            client = await self._ensure()
            if spec.function_code == 6:
                result = await client.write_register(spec.register_address, registers[0], **kwargs)
            elif spec.function_code == 16:
                result = await client.write_registers(spec.register_address, registers, **kwargs)
            else:
                raise ModbusError(
                    f"Funktionscode {spec.function_code} ist kein Schreibregister (Register '{spec.key}')"
                )
        if result.isError():
            raise ModbusError(f"Modbus-Fehler beim Schreiben von '{spec.key}': {result}")
