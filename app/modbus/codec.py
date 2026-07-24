"""Generische Kodierung/Dekodierung von Modbus-Registerwerten.

Dies ist die häufigste Fehlerquelle bei der Integration fremder Wallboxen,
daher ist die Logik bewusst klein, explizit und vollständig durch Unit-Tests
abgedeckt (siehe ``tests/test_codec.py``).

Modell:
    * Ein Modbus-Register ist 16 Bit breit (2 Byte).
    * ``word_order`` bestimmt bei Mehrwort-Typen (32/64 Bit), welches Register
      das höherwertige Wort enthält (``big`` = erstes Register ist MSW).
    * ``byte_order`` bestimmt innerhalb eines Registers, ob das High-Byte
      zuerst kommt (``big``, Modbus-Standard) oder das Low-Byte (``little``).

Physikalischer Wert = Rohwert * scale + offset  (beim Lesen)
Rohwert = round((phys - offset) / scale)          (beim Schreiben)
"""

from __future__ import annotations

import struct

from app.models.base import ByteOrder, DataType, WordOrder

# struct-Formatzeichen (immer big-endian interpretiert) und Registeranzahl
# je Datentyp. Die Endianness wird ausschließlich durch das nachträgliche
# Umsortieren der Bytes/Wörter abgebildet, nicht durch struct.
_TYPE_INFO: dict[DataType, tuple[str, int]] = {
    DataType.UINT16: (">H", 1),
    DataType.INT16: (">h", 1),
    DataType.UINT32: (">I", 2),
    DataType.INT32: (">i", 2),
    DataType.FLOAT32: (">f", 2),
    DataType.FLOAT64: (">d", 4),
    DataType.BOOL: (">H", 1),
}


def word_count(data_type: DataType) -> int:
    """Anzahl der 16-Bit-Register, die dieser Datentyp belegt."""
    return _TYPE_INFO[data_type][1]


def _normalize(value: str | ByteOrder | WordOrder) -> str:
    """Akzeptiert Enum oder String und liefert 'big'/'little'."""
    if isinstance(value, (ByteOrder, WordOrder)):
        return value.value
    return str(value)


def _reorder(canonical: bytes, byte_order: str, word_order: str) -> bytes:
    """Wandelt kanonische Big/Big-Bytes in die gewünschte Reihenfolge (oder zurück).

    Die Operation ist selbstinvers, solange dieselben Parameter genutzt werden:
    Byte-Swap innerhalb jedes Wortes und optionales Umkehren der Wortreihenfolge.
    """
    words = [canonical[i : i + 2] for i in range(0, len(canonical), 2)]
    if byte_order == "little":
        words = [w[::-1] for w in words]
    if word_order == "little":
        words = words[::-1]
    return b"".join(words)


def decode_registers(
    registers: list[int],
    data_type: DataType,
    byte_order: str | ByteOrder = ByteOrder.BIG,
    word_order: str | WordOrder = WordOrder.BIG,
    scale: float = 1.0,
    offset: float = 0.0,
) -> float | int | bool:
    """Dekodiert eine Registerliste in einen physikalischen Wert.

    :param registers: Rohregister (16-Bit-Ganzzahlen) in Adressreihenfolge.
    """
    fmt, count = _TYPE_INFO[data_type]
    if len(registers) < count:
        raise ValueError(
            f"{data_type.value} benötigt {count} Register, {len(registers)} erhalten"
        )
    registers = registers[:count]
    byte_order = _normalize(byte_order)
    word_order = _normalize(word_order)

    # Rohregister -> kanonische Bytefolge (jedes Register big-endian)
    raw = b"".join(struct.pack(">H", r & 0xFFFF) for r in registers)
    # Reihenfolge in kanonische Big/Big-Form zurückführen
    canonical = _reorder(raw, byte_order, word_order)
    (value,) = struct.unpack(fmt, canonical)

    if data_type is DataType.BOOL:
        return bool(value)

    physical = value * scale + offset
    # Ganzzahlige Ergebnisse ohne Nachkommaanteil auch als int zurückgeben
    if isinstance(physical, float) and physical.is_integer() and scale == 1.0 and offset == 0.0:
        return int(physical)
    return physical


def encode_value(
    value: float | int | bool,
    data_type: DataType,
    byte_order: str | ByteOrder = ByteOrder.BIG,
    word_order: str | WordOrder = WordOrder.BIG,
    scale: float = 1.0,
    offset: float = 0.0,
) -> list[int]:
    """Kodiert einen physikalischen Wert in eine Registerliste.

    Führt die Rück-Skalierung durch und rundet für Ganzzahltypen sauber.
    """
    fmt, count = _TYPE_INFO[data_type]
    byte_order = _normalize(byte_order)
    word_order = _normalize(word_order)

    if data_type is DataType.BOOL:
        raw_value: int = 1 if value else 0
    else:
        scaled = (value - offset) / scale if scale != 0 else (value - offset)
        if data_type in (DataType.FLOAT32, DataType.FLOAT64):
            raw_value = float(scaled)
        else:
            raw_value = int(round(scaled))
            raw_value = _clamp_to_type(raw_value, data_type)

    canonical = struct.pack(fmt, raw_value)
    reordered = _reorder(canonical, byte_order, word_order)
    return [struct.unpack(">H", reordered[i : i + 2])[0] for i in range(0, len(reordered), 2)]


def _clamp_to_type(value: int, data_type: DataType) -> int:
    """Begrenzt einen Integer auf den gültigen Bereich seines Modbus-Typs."""
    bounds = {
        DataType.UINT16: (0, 0xFFFF),
        DataType.INT16: (-0x8000, 0x7FFF),
        DataType.UINT32: (0, 0xFFFFFFFF),
        DataType.INT32: (-0x80000000, 0x7FFFFFFF),
    }
    lo, hi = bounds[data_type]
    return max(lo, min(hi, value))


def apply_enum(value: float | int | bool, enum_map: dict | None) -> str | None:
    """Wandelt einen Rohwert per ``enum_map`` in Klartext, falls vorhanden."""
    if not enum_map:
        return None
    # enum_map-Schlüssel sind Strings (JSON), Wert kann int/bool sein
    return enum_map.get(str(int(value)))
