"""Geräteprofil und Register-Mapping – das Herzstück der Konfiguration."""

from __future__ import annotations

from sqlalchemy import (
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    ByteOrder,
    DataType,
    RegisterRole,
    WordOrder,
)


class DeviceProfile(Base):
    """Beschreibt ein Wallbox-/Zähler-Modell über seine Modbus-Register-Map."""

    __tablename__ = "device_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    manufacturer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Modbus Slave-/Unit-ID, Vorgabe für Stationen dieses Modells
    default_unit_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Standard-Byte-/Wort-Reihenfolge (pro Register überschreibbar)
    byte_order: Mapped[ByteOrder] = mapped_column(
        Enum(ByteOrder), default=ByteOrder.BIG, nullable=False
    )
    word_order: Mapped[WordOrder] = mapped_column(
        Enum(WordOrder), default=WordOrder.BIG, nullable=False
    )

    registers: Mapped[list["RegisterMapping"]] = relationship(
        "RegisterMapping",
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="RegisterMapping.register_address",
    )

    def register_for(self, key: str) -> "RegisterMapping | None":
        """Liefert das Register-Mapping zu einem semantischen Schlüssel."""
        for reg in self.registers:
            if reg.key == key:
                return reg
        return None


class RegisterMapping(Base):
    """Einzelnes Register innerhalb eines Geräteprofils."""

    __tablename__ = "register_mappings"
    __table_args__ = (
        UniqueConstraint("profile_id", "key", name="uq_profile_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("device_profiles.id", ondelete="CASCADE"), nullable=False
    )

    # Semantischer Name, z. B. current_l1, active_power, set_current, enable
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    role: Mapped[RegisterRole] = mapped_column(Enum(RegisterRole), nullable=False)

    # Modbus-Adresse (dezimal) und Funktionscode (3/4/6/16)
    register_address: Mapped[int] = mapped_column(Integer, nullable=False)
    function_code: Mapped[int] = mapped_column(Integer, nullable=False)

    data_type: Mapped[DataType] = mapped_column(Enum(DataType), nullable=False)

    # Optional pro Register: überschreibt die Profil-Defaults
    byte_order: Mapped[ByteOrder | None] = mapped_column(Enum(ByteOrder), nullable=True)
    word_order: Mapped[WordOrder | None] = mapped_column(Enum(WordOrder), nullable=True)

    # Skalierung: physikalischer Wert = Rohwert * scale + offset
    scale: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    offset: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Rohwert -> Klartext (z. B. Statuscodes)
    enum_map: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Grenzen für Schreibregister (z. B. Ladestrom 6..32 A)
    writable_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    writable_max: Mapped[float | None] = mapped_column(Float, nullable=True)

    profile: Mapped[DeviceProfile] = relationship(
        "DeviceProfile", back_populates="registers"
    )

    @property
    def effective_byte_order(self) -> ByteOrder:
        """Byte-Reihenfolge inkl. Fallback auf den Profil-Default."""
        return self.byte_order or self.profile.byte_order

    @property
    def effective_word_order(self) -> WordOrder:
        """Wort-Reihenfolge inkl. Fallback auf den Profil-Default."""
        return self.word_order or self.profile.word_order
