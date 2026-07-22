"""Pydantic-Schemas für die REST-API (Validierung & (De-)Serialisierung)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.base import (
    ByteOrder,
    DataType,
    DistributionStrategy,
    ManagementMode,
    PhaseConfig,
    RegisterRole,
    SafeState,
    WordOrder,
)

# Gültige Funktionscodes je Rolle
_READ_FCS = {3, 4}
_WRITE_FCS = {6, 16}


# --- Register / Profil -----------------------------------------------------

class RegisterMappingBase(BaseModel):
    key: str = Field(..., max_length=60)
    role: RegisterRole
    register_address: int = Field(..., ge=0, le=65535)
    function_code: int
    data_type: DataType
    byte_order: ByteOrder | None = None
    word_order: WordOrder | None = None
    scale: float = 1.0
    offset: float = 0.0
    unit: str | None = Field(None, max_length=20)
    enum_map: dict | None = None
    writable_min: float | None = None
    writable_max: float | None = None

    @model_validator(mode="after")
    def _check_consistency(self):
        if self.role is RegisterRole.READ and self.function_code not in _READ_FCS:
            raise ValueError(
                f"Leseregister '{self.key}' benötigt Funktionscode 3 oder 4"
            )
        if self.role is RegisterRole.WRITE and self.function_code not in _WRITE_FCS:
            raise ValueError(
                f"Schreibregister '{self.key}' benötigt Funktionscode 6 oder 16"
            )
        if (
            self.writable_min is not None
            and self.writable_max is not None
            and self.writable_min > self.writable_max
        ):
            raise ValueError("writable_min darf nicht größer als writable_max sein")
        return self


class RegisterMappingCreate(RegisterMappingBase):
    pass


class RegisterMappingRead(RegisterMappingBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class DeviceProfileBase(BaseModel):
    name: str = Field(..., max_length=120)
    manufacturer: str | None = Field(None, max_length=120)
    notes: str | None = Field(None, max_length=1000)
    default_unit_id: int = Field(1, ge=0, le=255)
    byte_order: ByteOrder = ByteOrder.BIG
    word_order: WordOrder = WordOrder.BIG


class DeviceProfileCreate(DeviceProfileBase):
    registers: list[RegisterMappingCreate] = Field(default_factory=list)


class DeviceProfileUpdate(DeviceProfileBase):
    registers: list[RegisterMappingCreate] | None = None


class DeviceProfileRead(DeviceProfileBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    registers: list[RegisterMappingRead] = Field(default_factory=list)


# Import-/Export-Format (kompakt, wie im Prompt beschrieben)
class ProfileImport(BaseModel):
    name: str
    manufacturer: str | None = None
    notes: str | None = None
    default_unit_id: int = 1
    byte_order: ByteOrder = ByteOrder.BIG
    word_order: WordOrder = WordOrder.BIG
    registers: list[RegisterMappingCreate] = Field(default_factory=list)


# --- Ladestation -----------------------------------------------------------

class ChargingStationBase(BaseModel):
    name: str = Field(..., max_length=120)
    location: str | None = Field(None, max_length=120)
    ip_address: str = Field(..., max_length=60)
    tcp_port: int = Field(502, ge=1, le=65535)
    unit_id: int = Field(1, ge=0, le=255)
    profile_id: int
    phase_config: PhaseConfig = PhaseConfig.P3
    priority: int = 0
    max_current_a: float = Field(32.0, ge=0)
    min_current_a: float = Field(6.0, ge=0)
    enabled: bool = True
    safe_state: SafeState = SafeState.BLOCK

    @model_validator(mode="after")
    def _check_currents(self):
        if self.min_current_a > self.max_current_a:
            raise ValueError("min_current_a darf nicht größer als max_current_a sein")
        return self


class ChargingStationCreate(ChargingStationBase):
    pass


class ChargingStationUpdate(ChargingStationBase):
    pass


class ChargingStationRead(ChargingStationBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


# --- Globale Konfiguration -------------------------------------------------

class GlobalConfigBase(BaseModel):
    grid_limit_current_a: float = Field(63.0, ge=0)
    management_mode: ManagementMode = ManagementMode.STATIC
    distribution_strategy: DistributionStrategy = DistributionStrategy.EQUAL
    poll_interval_s: float = Field(3.0, gt=0)
    setpoint_min_change_a: float = Field(1.0, ge=0)
    setpoint_min_hold_s: float = Field(30.0, ge=0)
    fail_safe_after_s: float = Field(15.0, ge=0)
    dynamic_meter_enabled: bool = False
    meter_profile_id: int | None = None
    meter_ip_address: str | None = Field(None, max_length=60)
    meter_tcp_port: int = Field(502, ge=1, le=65535)
    meter_unit_id: int = Field(1, ge=0, le=255)
    en14a_enabled: bool = False
    en14a_active: bool = False
    en14a_limit_current_a: float = Field(6.0, ge=0)


class GlobalConfigRead(GlobalConfigBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class GlobalConfigUpdate(BaseModel):
    # Alle Felder optional – partielles Update
    grid_limit_current_a: float | None = Field(None, ge=0)
    management_mode: ManagementMode | None = None
    distribution_strategy: DistributionStrategy | None = None
    poll_interval_s: float | None = Field(None, gt=0)
    setpoint_min_change_a: float | None = Field(None, ge=0)
    setpoint_min_hold_s: float | None = Field(None, ge=0)
    fail_safe_after_s: float | None = Field(None, ge=0)
    dynamic_meter_enabled: bool | None = None
    meter_profile_id: int | None = None
    meter_ip_address: str | None = Field(None, max_length=60)
    meter_tcp_port: int | None = Field(None, ge=1, le=65535)
    meter_unit_id: int | None = Field(None, ge=0, le=255)
    en14a_enabled: bool | None = None
    en14a_active: bool | None = None
    en14a_limit_current_a: float | None = Field(None, ge=0)


# --- Live-/Status-Antworten ------------------------------------------------

class StationLive(BaseModel):
    station_id: int
    name: str
    online: bool
    values: dict = Field(default_factory=dict)
    setpoint_a: float | None = None
    error: str | None = None


class SystemStatus(BaseModel):
    management_mode: ManagementMode
    distribution_strategy: DistributionStrategy
    grid_limit_current_a: float
    effective_limit_current_a: float
    en14a_active: bool
    phase_load_a: dict[str, float]
    phase_available_a: dict[str, float]
    active_stations: int
    total_stations: int
    last_cycle: str | None = None
    stations: list[StationLive] = Field(default_factory=list)
