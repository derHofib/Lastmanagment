"""Prüfung signierter Voltibus-Lizenzschlüssel.

Schlüsselformat: ``VLTB1.<payload_b64url>.<sig_b64url>``. Der Payload ist
kompaktes JSON (``{"tier": "pro", "max_stations": null, "issued_to": "...",
"iat": "2026-07-23"}``), signiert mit Ed25519.

Bewusst **asymmetrisch** (nicht HMAC): Diese Datei ist Teil des offenen
Quellcodes, ihr ``PUBLIC_KEY_B64`` darf also jeder sehen. Mit einer
symmetrischen Signatur (HMAC) wäre das Geheimnis damit ebenfalls öffentlich
und jeder könnte sich selbst gültige Pro-Schlüssel ausstellen. Der private
Signaturschlüssel bleibt beim Herausgeber, siehe ``tools/licensing/keygen.py``
– er ist NICHT Teil dieses Repositories.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.models.base import LicenseTier

KEY_PREFIX = "VLTB1"

# Öffentlicher Prüfschlüssel (Ed25519, 32 Bytes, base64url-kodiert ohne
# Padding). Erzeugt über ``tools/licensing/keygen.py --generate-keypair``.
# Der zugehörige private Schlüssel wird NICHT im Repository abgelegt.
PUBLIC_KEY_B64 = "bzlN7hSge95w_ditFnCpcJDDqm168_mbnBlUZMX_O0M"

# Standard-Obergrenze für die Anzahl Ladestationen je Tier.
# None = unbegrenzt. Kann pro Schlüssel über "max_stations" im Payload
# explizit überschrieben werden (z. B. ein individuelles Pro-Kontingent).
TIER_LIMITS: dict[LicenseTier, int | None] = {
    LicenseTier.FREE: 2,
    LicenseTier.PRO: 10,
    LicenseTier.ENTERPRISE: None,
}


@dataclass(frozen=True)
class LicensePayload:
    tier: LicenseTier
    max_stations: int | None
    issued_to: str | None
    iat: str | None


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def verify_license_key(raw_key: str, public_key_b64: str | None = None) -> LicensePayload:
    """Prüft Signatur und Inhalt eines Lizenzschlüssels.

    ``public_key_b64`` ist parametrisierbar (statt immer die Modul-Konstante
    zu nutzen), damit Tests mit einem eigenen Schlüsselpaar arbeiten können,
    ohne den echten privaten Produktionsschlüssel zu benötigen.

    :raises ValueError: bei ungültigem Format, Signatur oder Inhalt.
    """
    parts = raw_key.strip().split(".")
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        raise ValueError("unbekanntes Schlüsselformat")

    _prefix, payload_b64, sig_b64 = parts
    try:
        payload_bytes = _b64url_decode(payload_b64)
        sig_bytes = _b64url_decode(sig_b64)
    except Exception as exc:  # noqa: BLE001 - beliebige Dekodierfehler -> ungültiger Schlüssel
        raise ValueError("Schlüssel ist nicht korrekt kodiert") from exc

    pub_b64 = public_key_b64 or PUBLIC_KEY_B64
    try:
        public_key = Ed25519PublicKey.from_public_bytes(_b64url_decode(pub_b64))
        public_key.verify(sig_bytes, payload_bytes)
    except InvalidSignature as exc:
        raise ValueError("Signatur ungültig") from exc
    except Exception as exc:  # noqa: BLE001 - z. B. falsche Schlüssellänge
        raise ValueError("Signaturprüfung fehlgeschlagen") from exc

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError("Payload ist kein gültiges JSON") from exc

    try:
        tier = LicenseTier(payload["tier"])
    except (KeyError, ValueError) as exc:
        raise ValueError("unbekannte Lizenzstufe im Schlüssel") from exc

    max_stations = payload.get("max_stations")
    if max_stations is not None and not isinstance(max_stations, int):
        raise ValueError("max_stations muss eine Zahl oder null sein")

    return LicensePayload(
        tier=tier,
        max_stations=max_stations,
        issued_to=payload.get("issued_to"),
        iat=payload.get("iat"),
    )


def effective_max_stations(tier: LicenseTier, max_stations_override: int | None) -> int | None:
    """Effektives Stationslimit: explizite Override geht vor Tier-Standard."""
    if max_stations_override is not None:
        return max_stations_override
    return TIER_LIMITS.get(tier, TIER_LIMITS[LicenseTier.FREE])
