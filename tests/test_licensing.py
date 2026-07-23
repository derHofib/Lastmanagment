"""Tests für app.licensing: Signaturprüfung signierter Lizenzschlüssel.

Verwendet ein eigenes, im Test erzeugtes Ed25519-Schlüsselpaar – NICHT den
echten Produktionsschlüssel (der ist nicht Teil des Repositories). Die
Prüffunktion nimmt den öffentlichen Schlüssel deshalb als Parameter entgegen.
"""

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.licensing import TIER_LIMITS, effective_max_stations, verify_license_key
from app.models.base import LicenseTier


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _public_b64(private_key: Ed25519PrivateKey) -> str:
    return _b64url(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def _sign(private_key: Ed25519PrivateKey, payload: dict) -> str:
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = private_key.sign(payload_bytes)
    return f"VLTB1.{_b64url(payload_bytes)}.{_b64url(signature)}"


@pytest.fixture()
def keypair():
    private_key = Ed25519PrivateKey.generate()
    return private_key, _public_b64(private_key)


def test_verify_valid_pro_key(keypair):
    private_key, pub_b64 = keypair
    key = _sign(private_key, {"tier": "pro", "max_stations": None, "issued_to": "Acme", "iat": "2026-01-01"})
    payload = verify_license_key(key, public_key_b64=pub_b64)
    assert payload.tier == LicenseTier.PRO
    assert payload.max_stations is None
    assert payload.issued_to == "Acme"


def test_verify_valid_key_with_explicit_max_stations_override(keypair):
    private_key, pub_b64 = keypair
    key = _sign(private_key, {"tier": "pro", "max_stations": 25, "issued_to": None, "iat": "2026-01-01"})
    payload = verify_license_key(key, public_key_b64=pub_b64)
    assert payload.max_stations == 25


def test_verify_rejects_tampered_payload(keypair):
    private_key, pub_b64 = keypair
    key = _sign(private_key, {"tier": "free", "max_stations": 2, "issued_to": None, "iat": "2026-01-01"})
    prefix, _payload_b64, sig_b64 = key.split(".")
    tampered_payload = _b64url(
        json.dumps(
            {"tier": "enterprise", "max_stations": None, "issued_to": None, "iat": "2026-01-01"},
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
    )
    tampered_key = f"{prefix}.{tampered_payload}.{sig_b64}"
    with pytest.raises(ValueError):
        verify_license_key(tampered_key, public_key_b64=pub_b64)


def test_verify_rejects_key_signed_with_different_private_key(keypair):
    private_key, _pub_b64 = keypair
    other_public_b64 = _public_b64(Ed25519PrivateKey.generate())
    key = _sign(private_key, {"tier": "pro", "max_stations": None, "issued_to": None, "iat": "2026-01-01"})
    with pytest.raises(ValueError):
        verify_license_key(key, public_key_b64=other_public_b64)


def test_verify_rejects_malformed_format():
    with pytest.raises(ValueError):
        verify_license_key("not-a-real-key")
    with pytest.raises(ValueError):
        verify_license_key("VLTB1.onlyonepart")


def test_verify_rejects_unknown_tier(keypair):
    private_key, pub_b64 = keypair
    key = _sign(private_key, {"tier": "ultimate", "max_stations": None, "issued_to": None, "iat": "2026-01-01"})
    with pytest.raises(ValueError):
        verify_license_key(key, public_key_b64=pub_b64)


def test_effective_max_stations_override_wins():
    assert effective_max_stations(LicenseTier.FREE, 5) == 5


def test_effective_max_stations_falls_back_to_tier_default():
    assert effective_max_stations(LicenseTier.FREE, None) == TIER_LIMITS[LicenseTier.FREE]
    assert effective_max_stations(LicenseTier.ENTERPRISE, None) is None
