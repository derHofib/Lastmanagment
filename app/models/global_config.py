"""Globale, zur Laufzeit änderbare Betriebskonfiguration (Singleton-Zeile)."""

from __future__ import annotations

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models.base import Base, DistributionStrategy, ManagementMode


class GlobalConfig(Base):
    """Systemweite Einstellungen. Es existiert genau eine Zeile (id=1)."""

    __tablename__ = "global_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # Harte Obergrenze für den Gesamtstrom pro Phase (z. B. Hausanschluss 63 A)
    grid_limit_current_a: Mapped[float] = mapped_column(
        Float, default=63.0, nullable=False
    )

    management_mode: Mapped[ManagementMode] = mapped_column(
        Enum(ManagementMode), default=ManagementMode.STATIC, nullable=False
    )
    distribution_strategy: Mapped[DistributionStrategy] = mapped_column(
        Enum(DistributionStrategy), default=DistributionStrategy.EQUAL, nullable=False
    )

    poll_interval_s: Mapped[float] = mapped_column(Float, default=3.0, nullable=False)

    # --- Hysterese / Schaltzeiten (Relais-Flattern vermeiden) ---
    # Sollwertänderungen kleiner als dieser Betrag werden ignoriert
    setpoint_min_change_a: Mapped[float] = mapped_column(
        Float, default=1.0, nullable=False
    )
    # Mindesthaltezeit eines Sollwerts, bevor er wieder verändert wird
    setpoint_min_hold_s: Mapped[float] = mapped_column(
        Float, default=30.0, nullable=False
    )

    # --- Fail-Safe ---
    # Nach so vielen Sekunden ohne gültige Kommunikation -> sicherer Zustand
    fail_safe_after_s: Mapped[float] = mapped_column(
        Float, default=15.0, nullable=False
    )

    # --- Dynamisches Lastmanagement (Netzanschlusszähler) ---
    dynamic_meter_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    meter_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("device_profiles.id"), nullable=True
    )
    meter_ip_address: Mapped[str | None] = mapped_column(String(60), nullable=True)
    meter_tcp_port: Mapped[int] = mapped_column(Integer, default=502, nullable=False)
    meter_unit_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # --- §14a EnWG (steuerbare Verbrauchseinrichtung) ---
    en14a_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Aktives Steuersignal (per API-Trigger gesetzt). Alternativ könnte hier eine
    # Register-Signalquelle ausgewertet werden – siehe loadmanager/loop.py.
    en14a_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Reduzierte Gesamtstromgrenze pro Phase bei aktivem §14a-Signal
    # (z. B. 4,2 kW gesamt ~ 6 A pro Phase bei 3~230/400 V)
    en14a_limit_current_a: Mapped[float] = mapped_column(
        Float, default=6.0, nullable=False
    )

    # --- Cloud-Anbindung (Voltibus Cloud, optionales "Phone-Home") ---
    # Sendet periodisch den Status (wie GET /api/status) an einen selbst
    # gehosteten Cloud-Dienst (siehe cloud/), rein für die Fernansicht.
    # Ausfälle der Cloud-Verbindung beeinflussen die lokale Regelung nie
    # (siehe app.loadmanager.cloud_relay).
    cloud_relay_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cloud_relay_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cloud_relay_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cloud_relay_interval_s: Mapped[float] = mapped_column(Float, default=30.0, nullable=False)

    # --- MQTT-Anbindung (Status veröffentlichen, z. B. für Home Assistant) ---
    # Rein additiv: veröffentlicht periodisch denselben Stand wie
    # GET /api/status; Verbindungsfehler beeinflussen die Regelung nie
    # (siehe app.loadmanager.mqtt_publisher).
    mqtt_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mqtt_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mqtt_port: Mapped[int] = mapped_column(Integer, default=1883, nullable=False)
    mqtt_username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mqtt_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mqtt_topic_prefix: Mapped[str] = mapped_column(String(120), default="voltibus", nullable=False)
    mqtt_interval_s: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    # Home-Assistant-MQTT-Discovery mitsenden (retained Discovery-Topics,
    # damit Sensoren automatisch in Home Assistant erscheinen)
    mqtt_ha_discovery: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # --- Anpassbares Dashboard ---
    # JSON-Array [{"id": "phase-bars", "visible": true}, ...] in Anzeige-
    # reihenfolge. None = Standardreihenfolge, alle Karten sichtbar.
    dashboard_layout: Mapped[str | None] = mapped_column(String, nullable=True)

    # --- Baukasten-Topologie ---
    # Vom Nutzer hochgeladenes Hintergrundbild (z. B. Foto des Standorts) für
    # den Topologie-Canvas, als data:-URL gespeichert. None = kein Bild
    # (Punktraster-Standardhintergrund).
    topology_background_image: Mapped[str | None] = mapped_column(String, nullable=True)

    @staticmethod
    def get_or_create(session: Session) -> "GlobalConfig":
        """Liefert die Singleton-Konfiguration, erzeugt sie bei Bedarf."""
        cfg = session.get(GlobalConfig, 1)
        if cfg is None:
            cfg = GlobalConfig(id=1)
            session.add(cfg)
            session.commit()
            session.refresh(cfg)
        return cfg
