"""Deklarative Basis und gemeinsame Typ-Enums für alle ORM-Modelle."""

from __future__ import annotations

import enum

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Gemeinsame deklarative Basis."""


class RegisterRole(str, enum.Enum):
    """Rolle eines Registers: Messwert (read) oder Steuerung (write)."""

    READ = "read"
    WRITE = "write"


class DataType(str, enum.Enum):
    """Unterstützte Modbus-Datentypen."""

    UINT16 = "uint16"
    INT16 = "int16"
    UINT32 = "uint32"
    INT32 = "int32"
    FLOAT32 = "float32"
    FLOAT64 = "float64"
    BOOL = "bool"


class ByteOrder(str, enum.Enum):
    """Byte-Reihenfolge innerhalb eines 16-Bit-Registers."""

    BIG = "big"
    LITTLE = "little"


class WordOrder(str, enum.Enum):
    """Wort-Reihenfolge bei Mehrwort-Datentypen (32/64 Bit)."""

    BIG = "big"
    LITTLE = "little"


class PhaseConfig(str, enum.Enum):
    """Phasenanschluss der Ladestation."""

    P1_L1 = "1p_l1"
    P1_L2 = "1p_l2"
    P1_L3 = "1p_l3"
    P3 = "3p"


class ManagementMode(str, enum.Enum):
    """Betriebsmodus des Lastmanagements."""

    STATIC = "static"
    DYNAMIC = "dynamic"


class DistributionStrategy(str, enum.Enum):
    """Verteilstrategie für den verfügbaren Strom."""

    EQUAL = "equal"
    PRIORITY = "priority"
    FIFO = "fifo"


class SafeState(str, enum.Enum):
    """Sicherer Zustand einer Station bei Kommunikationsverlust/Fehler."""

    # Auf Minimalstrom begrenzen (Fahrzeug lädt langsam weiter)
    MIN_CURRENT = "min_current"
    # Station sperren (0 A / enable=0)
    BLOCK = "block"


class LicenseTier(str, enum.Enum):
    """Lizenzstufe. Free hat vollen Funktionsumfang, nur die Anzahl der
    Ladestationen ist begrenzt (siehe app.licensing.TIER_LIMITS)."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"
