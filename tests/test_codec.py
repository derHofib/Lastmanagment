"""Unit-Tests für die Modbus-Kodierung/-Dekodierung.

Geprüft werden alle Datentypen inkl. Byte-/Wort-Reihenfolge und Skalierung
gegen bekannte Roh-/Sollwert-Paare. Dies ist die kritischste Logik der
Anwendung – daher hohe Testabdeckung.
"""

import struct

import pytest

from app.models.base import DataType
from app.modbus import codec


# --- Registeranzahl je Datentyp -------------------------------------------

@pytest.mark.parametrize(
    "dtype,count",
    [
        (DataType.UINT16, 1),
        (DataType.INT16, 1),
        (DataType.BOOL, 1),
        (DataType.UINT32, 2),
        (DataType.INT32, 2),
        (DataType.FLOAT32, 2),
        (DataType.FLOAT64, 4),
    ],
)
def test_word_count(dtype, count):
    assert codec.word_count(dtype) == count


# --- 16-Bit-Typen ----------------------------------------------------------

def test_uint16_decode():
    assert codec.decode_registers([1234], DataType.UINT16) == 1234


def test_int16_negative():
    # 0xFFFF = -1 als int16
    assert codec.decode_registers([0xFFFF], DataType.INT16) == -1


def test_bool_decode():
    assert codec.decode_registers([1], DataType.BOOL) is True
    assert codec.decode_registers([0], DataType.BOOL) is False


# --- Skalierung & Offset ---------------------------------------------------

def test_scale_applied_on_read():
    # Rohwert 105 * 0.1 = 10.5 A
    assert codec.decode_registers([105], DataType.UINT16, scale=0.1) == pytest.approx(10.5)


def test_offset_applied_on_read():
    assert codec.decode_registers([100], DataType.INT16, scale=1.0, offset=-50) == 50


def test_scale_roundtrip_write():
    regs = codec.encode_value(10.5, DataType.UINT16, scale=0.1)
    assert regs == [105]
    assert codec.decode_registers(regs, DataType.UINT16, scale=0.1) == pytest.approx(10.5)


# --- float32: Byte-/Wort-Reihenfolge --------------------------------------

def _float32_regs(value, byte_order, word_order):
    """Hilfsfunktion: erzeugt Register für einen float32 in gewünschter Order."""
    canonical = struct.pack(">f", value)  # big/big
    words = [canonical[0:2], canonical[2:4]]
    if byte_order == "little":
        words = [w[::-1] for w in words]
    if word_order == "little":
        words = words[::-1]
    data = b"".join(words)
    return [struct.unpack(">H", data[0:2])[0], struct.unpack(">H", data[2:4])[0]]


@pytest.mark.parametrize("byte_order", ["big", "little"])
@pytest.mark.parametrize("word_order", ["big", "little"])
def test_float32_all_orders(byte_order, word_order):
    value = 3.14159
    regs = _float32_regs(value, byte_order, word_order)
    decoded = codec.decode_registers(
        regs, DataType.FLOAT32, byte_order=byte_order, word_order=word_order
    )
    assert decoded == pytest.approx(value, rel=1e-5)


def test_float32_big_big_known_value():
    # 1.0f = 0x3F800000 -> Register [0x3F80, 0x0000] in big/big
    assert codec.decode_registers([0x3F80, 0x0000], DataType.FLOAT32) == pytest.approx(1.0)


def test_float32_word_swap_known_value():
    # 1.0f mit Word-Swap -> [0x0000, 0x3F80]
    val = codec.decode_registers(
        [0x0000, 0x3F80], DataType.FLOAT32, word_order="little"
    )
    assert val == pytest.approx(1.0)


# --- 32-Bit-Ganzzahlen -----------------------------------------------------

def test_uint32_big_big():
    # 70000 = 0x00011170 -> [0x0001, 0x1170]
    assert codec.decode_registers([0x0001, 0x1170], DataType.UINT32) == 70000


def test_uint32_energy_scale():
    # Rohwert 12345 * 0.1 = 1234.5 kWh
    regs = [0x0000, 0x3039]  # 12345
    assert codec.decode_registers(regs, DataType.UINT32, scale=0.1) == pytest.approx(1234.5)


def test_int32_negative():
    raw = struct.unpack(">HH", struct.pack(">i", -100000))
    assert codec.decode_registers(list(raw), DataType.INT32) == -100000


# --- float64 ---------------------------------------------------------------

def test_float64_roundtrip():
    value = 123456.789
    regs = codec.encode_value(value, DataType.FLOAT64)
    assert len(regs) == 4
    assert codec.decode_registers(regs, DataType.FLOAT64) == pytest.approx(value)


# --- Vollständiger Roundtrip über alle Typen und Orders --------------------

@pytest.mark.parametrize(
    "dtype,value",
    [
        (DataType.UINT16, 65535),
        (DataType.INT16, -12345),
        (DataType.UINT32, 4000000000),
        (DataType.INT32, -2000000000),
        (DataType.FLOAT32, -273.15),
        (DataType.FLOAT64, 6.022e23),
    ],
)
@pytest.mark.parametrize("byte_order", ["big", "little"])
@pytest.mark.parametrize("word_order", ["big", "little"])
def test_full_roundtrip(dtype, value, byte_order, word_order):
    regs = codec.encode_value(value, dtype, byte_order=byte_order, word_order=word_order)
    decoded = codec.decode_registers(
        regs, dtype, byte_order=byte_order, word_order=word_order
    )
    if dtype in (DataType.FLOAT32,):
        assert decoded == pytest.approx(value, rel=1e-5)
    elif dtype in (DataType.FLOAT64,):
        assert decoded == pytest.approx(value, rel=1e-12)
    else:
        assert decoded == value


# --- Schreibgrenzen (Clamping erfolgt im Client, hier Encoding-Grenzen) ----

def test_encode_clamps_to_type_range():
    # 70000 passt nicht in uint16 -> wird auf 0xFFFF begrenzt
    regs = codec.encode_value(70000, DataType.UINT16)
    assert regs == [0xFFFF]


def test_encode_rounds_integer():
    regs = codec.encode_value(6.6, DataType.UINT16)
    assert regs == [7]


# --- enum_map --------------------------------------------------------------

def test_apply_enum():
    m = {"0": "Verfügbar", "2": "Lädt"}
    assert codec.apply_enum(2, m) == "Lädt"
    assert codec.apply_enum(0, m) == "Verfügbar"
    assert codec.apply_enum(9, m) is None
    assert codec.apply_enum(2, None) is None


# --- Fehlerfälle -----------------------------------------------------------

def test_decode_too_few_registers_raises():
    with pytest.raises(ValueError):
        codec.decode_registers([1], DataType.UINT32)
