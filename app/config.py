"""Globale Anwendungskonfiguration.

Werte werden aus Umgebungsvariablen und optional aus einer ``config.yaml``
gelesen. Die betrieblichen Grenzwerte (Gesamtstrom, Modus, Strategie) liegen
zusätzlich in der Datenbank (:class:`app.models.GlobalConfig`), damit sie zur
Laufzeit über die Weboberfläche änderbar sind. Diese Datei enthält nur die
Infrastruktur-Einstellungen (Datenbankpfad, Netzwerk-Timeouts, Zeitzone).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # PyYAML ist optional – ohne Datei laufen wir mit Defaults weiter
    import yaml
except ImportError:  # pragma: no cover - nur wenn PyYAML fehlt
    yaml = None


# Zeitzone für alle Zeitstempel (Anforderung: Europe/Berlin)
TIMEZONE = "Europe/Berlin"

# Basisverzeichnis des Projekts
BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    """Infrastruktur-Einstellungen der Anwendung."""

    # Persistenz
    database_url: str = "sqlite:///lastmanagement.db"

    # HTTP-Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Modbus – Netzwerk-Defaults (pro Station in der DB überschreibbar)
    modbus_timeout_s: float = 3.0
    modbus_retries: int = 2

    # Regelzyklus / Polling
    default_poll_interval_s: float = 3.0

    # Logging
    log_level: str = "INFO"
    log_file: str | None = None

    # Zeitzone
    timezone: str = TIMEZONE

    def apply_yaml(self, path: Path) -> "Settings":
        """Überschreibt Felder aus einer YAML-Datei, falls vorhanden."""
        if yaml is None or not path.exists():
            return self
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)
        return self

    def apply_env(self) -> "Settings":
        """Überschreibt Felder aus Umgebungsvariablen (Präfix ``LM_``)."""
        mapping = {
            "LM_DATABASE_URL": ("database_url", str),
            "LM_HOST": ("host", str),
            "LM_PORT": ("port", int),
            "LM_MODBUS_TIMEOUT_S": ("modbus_timeout_s", float),
            "LM_MODBUS_RETRIES": ("modbus_retries", int),
            "LM_POLL_INTERVAL_S": ("default_poll_interval_s", float),
            "LM_LOG_LEVEL": ("log_level", str),
            "LM_LOG_FILE": ("log_file", str),
        }
        for env_key, (attr, caster) in mapping.items():
            raw = os.environ.get(env_key)
            if raw is not None and raw != "":
                setattr(self, attr, caster(raw))
        return self


def load_settings() -> Settings:
    """Lädt die Einstellungen: Defaults -> config.yaml -> Umgebungsvariablen."""
    settings = Settings()
    config_path = Path(os.environ.get("LM_CONFIG", BASE_DIR / "config.yaml"))
    settings.apply_yaml(config_path)
    settings.apply_env()
    return settings


# Modulweite Singleton-Instanz
settings = load_settings()
