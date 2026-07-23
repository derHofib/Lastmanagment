"""Infrastruktur-Einstellungen des Cloud-Diensts (getrennt von app/config.py)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


@dataclass
class CloudSettings:
    database_url: str = "sqlite:///voltibus_cloud.db"
    host: str = "0.0.0.0"
    port: int = 9000

    # Signaturschlüssel für Session-JWTs. Ohne LMC_JWT_SECRET wird beim Start
    # ein zufälliger Schlüssel erzeugt – Sessions überleben dann KEINEN
    # Neustart (alle Nutzer müssen sich neu anmelden). Für den Produktivbetrieb
    # zwingend einen festen Wert setzen (siehe README-cloud.md).
    jwt_secret: str = ""
    jwt_secret_is_random: bool = False
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: float = 24 * 14  # 14 Tage

    # Session-Cookie nur über HTTPS senden. Default False, damit lokales
    # Testen/Entwickeln über http:// funktioniert. Hinter einem TLS-
    # terminierenden Reverse-Proxy (siehe README-cloud.md) auf true setzen.
    cookie_secure: bool = False

    log_level: str = "INFO"


def load_settings() -> CloudSettings:
    settings = CloudSettings()
    settings.database_url = os.environ.get("LMC_DATABASE_URL", settings.database_url)
    settings.host = os.environ.get("LMC_HOST", settings.host)
    settings.port = int(os.environ.get("LMC_PORT", settings.port))
    env_secret = os.environ.get("LMC_JWT_SECRET", "")
    settings.jwt_secret = env_secret or secrets.token_urlsafe(32)
    settings.jwt_secret_is_random = not env_secret
    settings.cookie_secure = os.environ.get("LMC_COOKIE_SECURE", "").lower() in ("1", "true", "yes")
    settings.log_level = os.environ.get("LMC_LOG_LEVEL", settings.log_level)
    return settings


settings = load_settings()
