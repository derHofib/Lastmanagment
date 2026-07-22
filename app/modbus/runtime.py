"""Entkoppelte Laufzeit-Datenstrukturen für die Modbus-Schicht.

Der Regelzyklus läuft asynchron und darf keine ORM-Objekte über ``await``
hinweg festhalten (Session-Lebensdauer!). Deshalb werden Profile/Stationen in
unveränderliche Dataclasses überführt, die frei von DB-Bezügen sind.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.base import DataType, RegisterRole
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
    """Aufgelöste Ladestation inkl. Verbindungs- und Profildaten."""

    id: int
    name: str
    ip_address: str
    tcp_port: int
    unit_id: int
    profile: ProfileSpec
    phases: tuple[str, ...]
    priority: int
    max_current_a: float
    min_current_a: float
    safe_state: str

    @classmethod
    def from_station(cls, station: ChargingStation) -> "StationSpec":
        return cls(
            id=station.id,
            name=station.name,
            ip_address=station.ip_address,
            tcp_port=station.tcp_port,
            unit_id=station.unit_id,
            profile=ProfileSpec.from_profile(station.profile),
            phases=station.phases,
            priority=station.priority,
            max_current_a=station.max_current_a,
            min_current_a=station.min_current_a,
            safe_state=station.safe_state.value,
        )
