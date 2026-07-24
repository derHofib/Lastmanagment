"""Pydantic-Schemas für die REST-API (Validierung & (De-)Serialisierung)."""

from __future__ import annotations

from datetime import datetime
from datetime import time as dt_time

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.base import (
    ByteOrder,
    DataType,
    DistributionStrategy,
    LicenseTier,
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


# --- Verteilungshierarchie ---------------------------------------------

class DistributionBoardBase(BaseModel):
    name: str = Field(..., max_length=120)
    parent_board_id: int | None = None
    incoming_fuse_a: float = Field(63.0, ge=0)
    priority: int = 0
    # Verteilstrategie für DIESEN Verteiler-Zweig. None = erbt vom
    # übergeordneten Verteiler bzw. zuletzt von der globalen Strategie.
    strategy: DistributionStrategy | None = None
    location: str | None = Field(None, max_length=120)
    notes: str | None = Field(None, max_length=1000)
    # Position im Baukasten-Topologie-Canvas. None = noch nicht platziert.
    canvas_x: float | None = None
    canvas_y: float | None = None


class DistributionBoardCreate(DistributionBoardBase):
    pass


class DistributionBoardUpdate(DistributionBoardBase):
    pass


class DistributionBoardRead(DistributionBoardBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class BoardTreeStation(BaseModel):
    """Ein Ladepunkt als Blatt im Verteilungsbaum, inkl. Live-Auslastung."""

    id: int
    name: str
    circuit_breaker_a: float | None = None
    max_current_a: float
    online: bool = False
    setpoint_a: float | None = None
    pv_surplus_only: bool = False


class BoardTreeNode(BaseModel):
    """Ein Verteiler-Knoten im Baum, rekursiv mit Kindern und Stationen."""

    id: int
    name: str
    incoming_fuse_a: float
    priority: int
    strategy: DistributionStrategy | None = None
    location: str | None = None
    # Aktuelle Auslastung je Phase innerhalb des gesamten Teilbaums
    load_a: dict[str, float] = Field(default_factory=dict)
    stations: list[BoardTreeStation] = Field(default_factory=list)
    children: list["BoardTreeNode"] = Field(default_factory=list)


BoardTreeNode.model_rebuild()


# --- Ladestation (physische Modbus-Verbindung) -----------------------------
#
# Eine Ladestation ist nur noch die physische Verbindung (IP/Port/Unit-ID +
# Profil). Die steuerbaren/zuteilbaren Ladepunkte (Priorität, Phasen,
# Min/Max-Strom, Verteiler, Zeitpläne, PV-Überschuss, ...) sind eigene
# ChargePoint-Einträge (siehe unten) – bei Doppel-Wallboxen zwei je Station.

class ChargingStationBase(BaseModel):
    name: str = Field(..., max_length=120)
    location: str | None = Field(None, max_length=120)
    ip_address: str = Field(..., max_length=60)
    tcp_port: int = Field(502, ge=1, le=65535)
    unit_id: int = Field(1, ge=0, le=255)
    profile_id: int
    # Position im Baukasten-Topologie-Canvas. None = noch nicht platziert.
    canvas_x: float | None = None
    canvas_y: float | None = None


class ChargingStationCreate(ChargingStationBase):
    pass


class ChargingStationUpdate(ChargingStationBase):
    pass


class ChargingStationRead(ChargingStationBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


# --- Zeitsteuerung (wiederkehrende Sperrfenster je Ladepunkt) --------------

class ChargeScheduleBase(BaseModel):
    # Bit 0 = Montag ... Bit 6 = Sonntag (1 = Fenster gilt an diesem Tag)
    weekdays_mask: int = Field(0b1111111, ge=0, le=127)
    start_time: dt_time
    end_time: dt_time


class ChargeScheduleCreate(ChargeScheduleBase):
    pass


class ChargeScheduleRead(ChargeScheduleBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


# --- Ladepunkt (steuerbare/zuteilbare Einheit einer Ladestation) -----------

class ChargePointBase(BaseModel):
    station_id: int
    # "" für Einzel-Ladepunkt-Stationen, "_1"/"_2" ... für Doppel-Wallboxen –
    # bestimmt, welche Register im Profil zu diesem Ladepunkt gehören.
    connector_suffix: str = Field("", max_length=10)
    name: str = Field(..., max_length=120)
    phase_config: PhaseConfig = PhaseConfig.P3
    priority: int = 0
    max_current_a: float = Field(32.0, ge=0)
    min_current_a: float = Field(6.0, ge=0)
    # An welchem Verteiler hängt der Abgang zu diesem Ladepunkt? None = Wurzel
    # (Hauptverteilung).
    distribution_board_id: int | None = None
    # Absicherung DES ABGANGS (Installationssicherung), separat von
    # max_current_a (technische Grenze der Wallbox selbst).
    circuit_breaker_a: float | None = Field(None, ge=0)
    enabled: bool = True
    safe_state: SafeState = SafeState.BLOCK
    # PV-Überschussladen: lädt nachrangig ausschließlich mit PV-Überschuss
    # (erfordert dynamisches Lastmanagement + Netzanschlusszähler).
    pv_surplus_only: bool = False

    @model_validator(mode="after")
    def _check_currents(self):
        if self.min_current_a > self.max_current_a:
            raise ValueError("min_current_a darf nicht größer als max_current_a sein")
        return self


class ChargePointCreate(ChargePointBase):
    schedules: list[ChargeScheduleCreate] = Field(default_factory=list)


class ChargePointUpdate(ChargePointBase):
    schedules: list[ChargeScheduleCreate] | None = None


class ChargePointRead(ChargePointBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    schedules: list[ChargeScheduleRead] = Field(default_factory=list)


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
    cloud_relay_enabled: bool = False
    cloud_relay_url: str | None = Field(None, max_length=255)
    cloud_relay_token: str | None = Field(None, max_length=255)
    cloud_relay_interval_s: float = Field(30.0, gt=0)
    mqtt_enabled: bool = False
    mqtt_host: str | None = Field(None, max_length=255)
    mqtt_port: int = Field(1883, ge=1, le=65535)
    mqtt_username: str | None = Field(None, max_length=120)
    mqtt_password: str | None = Field(None, max_length=255)
    mqtt_topic_prefix: str = Field("voltibus", max_length=120)
    mqtt_interval_s: float = Field(10.0, gt=0)
    mqtt_ha_discovery: bool = True
    # JSON-Array [{"id": "phase-bars", "visible": true}, ...] in Anzeige-
    # reihenfolge der Dashboard-Karten. None = Standardreihenfolge.
    dashboard_layout: str | None = None


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
    cloud_relay_enabled: bool | None = None
    cloud_relay_url: str | None = Field(None, max_length=255)
    cloud_relay_token: str | None = Field(None, max_length=255)
    cloud_relay_interval_s: float | None = Field(None, gt=0)
    mqtt_enabled: bool | None = None
    mqtt_host: str | None = Field(None, max_length=255)
    mqtt_port: int | None = Field(None, ge=1, le=65535)
    mqtt_username: str | None = Field(None, max_length=120)
    mqtt_password: str | None = Field(None, max_length=255)
    mqtt_topic_prefix: str | None = Field(None, max_length=120)
    mqtt_interval_s: float | None = Field(None, gt=0)
    mqtt_ha_discovery: bool | None = None
    dashboard_layout: str | None = None


# --- Lizenz ------------------------------------------------------------

class LicenseRead(BaseModel):
    tier: LicenseTier
    # Effektive Obergrenze für Ladestationen; None = unbegrenzt
    max_stations: int | None
    used_stations: int
    issued_to: str | None = None
    activated_at: datetime | None = None


class LicenseActivate(BaseModel):
    key: str = Field(..., min_length=1, max_length=1024)


# --- Live-/Status-Antworten ------------------------------------------------

class ConnectionTestResult(BaseModel):
    """Ergebnis eines Verbindungs-/Profiltests einer Ladestation (rohe,
    unskopierte Registerwerte – ggf. mit Connector-Suffix bei Doppel-
    Wallboxen, siehe app.modbus.runtime.ChargePointSpec)."""

    station_id: int
    name: str
    online: bool
    values: dict = Field(default_factory=dict)
    error: str | None = None


class ChargePointLive(BaseModel):
    charge_point_id: int
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
    # PV-Überschuss (Einspeisung) je Phase, aus dem Netzanschlusszähler
    # abgeleitet (nur > 0 im dynamischen Modus mit angeschlossenem Zähler).
    phase_surplus_a: dict[str, float]
    active_charge_points: int
    total_charge_points: int
    last_cycle: str | None = None
    charge_points: list[ChargePointLive] = Field(default_factory=list)
