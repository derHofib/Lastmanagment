"""Entkoppelte Laufzeit-Datenstrukturen für die Modbus-Schicht.

Der Regelzyklus läuft asynchron und darf keine ORM-Objekte über ``await``
hinweg festhalten (Session-Lebensdauer!). Deshalb werden Profile/Stationen/
Ladepunkte in unveränderliche Dataclasses überführt, die frei von DB-Bezügen
sind.

``StationSpec`` beschreibt die physische Modbus-TCP-Verbindung (ein Client
pro Station). ``ChargePointSpec`` beschreibt einen einzelnen steuerbaren
Ladepunkt an dieser Verbindung – bei Doppel-Wallboxen gibt es zwei je
Station, unterschieden durch ``connector_suffix`` (siehe
app.models.charge_point).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.base import DataType, RegisterRole
from app.models.charge_point import ChargePoint
from app.models.charging_station import ChargingStation
from app.models.device_profile import DeviceProfile, RegisterMapping


@dataclass(frozen=True)
class RegisterSpec:
    """Vollständige, aufgelöste Beschreibung eines Registers."""

    key: str
    role: RegisterRole
    register_address: int
    function_code: int
    data_type: DataType
    byte_order: str
    word_order: str
    scale: float = 1.0
    offset: float = 0.0
    unit: str | None = None
    enum_map: dict | None = None
    writable_min: float | None = None
    writable_max: float | None = None

    @classmethod
    def from_mapping(cls, m: RegisterMapping) -> "RegisterSpec":
        return cls(
            key=m.key,
            role=m.role,
            register_address=m.register_address,
            function_code=m.function_code,
            data_type=m.data_type,
            byte_order=m.effective_byte_order.value,
            word_order=m.effective_word_order.value,
            scale=m.scale,
            offset=m.offset,
            unit=m.unit,
            enum_map=dict(m.enum_map) if m.enum_map else None,
            writable_min=m.writable_min,
            writable_max=m.writable_max,
        )


@dataclass(frozen=True)
class ProfileSpec:
    """Aufgelöstes Geräteprofil als Sammlung von Registern."""

    name: str
    registers: dict[str, RegisterSpec] = field(default_factory=dict)

    @classmethod
    def from_profile(cls, profile: DeviceProfile) -> "ProfileSpec":
        return cls(
            name=profile.name,
            registers={r.key: RegisterSpec.from_mapping(r) for r in profile.registers},
        )

    def reads(self) -> list[RegisterSpec]:
        return [r for r in self.registers.values() if r.role is RegisterRole.READ]

    def writes(self) -> list[RegisterSpec]:
        return [r for r in self.registers.values() if r.role is RegisterRole.WRITE]


@dataclass(frozen=True)
class StationSpec:
    """Aufgelöste physische Ladestation: Modbus-TCP-Verbindung + Profil.

    Enthält bewusst KEINE Regel-Parameter mehr (Priorität, Phasen, Min/Max-
    Strom, Verteiler, ...) – die gehören zu den ChargePointSpec-Ladepunkten
    dieser Station.
    """

    id: int
    name: str
    ip_address: str
    tcp_port: int
    unit_id: int
    profile: ProfileSpec

    @classmethod
    def from_station(cls, station: ChargingStation) -> "StationSpec":
        return cls(
            id=station.id,
            name=station.name,
            ip_address=station.ip_address,
            tcp_port=station.tcp_port,
            unit_id=station.unit_id,
            profile=ProfileSpec.from_profile(station.profile),
        )


@dataclass(frozen=True)
class ChargePointSpec:
    """Aufgelöster, steuerbarer Ladepunkt (Anschluss einer Ladestation)."""

    id: int
    name: str
    station_id: int
    connector_suffix: str
    phases: tuple[str, ...]
    priority: int
    max_current_a: float
    min_current_a: float
    safe_state: str
    distribution_board_id: int | None
    pv_surplus_only: bool

    @classmethod
    def from_charge_point(cls, cp: ChargePoint) -> "ChargePointSpec":
        # Effektive Obergrenze: das Kleinere aus technischer Wallbox-Grenze
        # und Abgangs-Absicherung (falls hinterlegt) – niemals mehr als die
        # Installation zulässt.
        effective_max = cp.max_current_a
        if cp.circuit_breaker_a is not None:
            effective_max = min(effective_max, cp.circuit_breaker_a)
        return cls(
            id=cp.id,
            name=cp.name,
            station_id=cp.station_id,
            connector_suffix=cp.connector_suffix,
            phases=cp.phases,
            priority=cp.priority,
            max_current_a=effective_max,
            min_current_a=cp.min_current_a,
            safe_state=cp.safe_state.value,
            distribution_board_id=cp.distribution_board_id,
            pv_surplus_only=cp.pv_surplus_only,
        )

    def register(self, profile: ProfileSpec, base_key: str) -> RegisterSpec | None:
        """Löst einen semantischen Basis-Schlüssel (z. B. ``set_current``) auf
        das für diesen Ladepunkt zuständige, ggf. suffigierte Register auf."""
        return profile.registers.get(base_key + self.connector_suffix)

    def value(self, values: dict, base_key: str) -> object:
        """Liest einen Wert aus einem ``read_all()``-Ergebnis unter
        Berücksichtigung des Connector-Suffix dieses Ladepunkts."""
        return values.get(base_key + self.connector_suffix)
