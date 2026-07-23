#!/usr/bin/env python3
"""Vendor-Tool zum Erzeugen und Signieren von Voltibus-Lizenzschlüsseln.

NICHT Teil der laufenden Anwendung – nur für den Herausgeber der Software.
Der private Schlüssel darf NIEMALS ins Repository committet werden (siehe
.gitignore: ``*.pem``, ``license_private_key*``).

Verwendung:

    # Einmalig: Schlüsselpaar erzeugen. Den ausgegebenen öffentlichen
    # Schlüssel danach in app/licensing.py::PUBLIC_KEY_B64 eintragen.
    python -m tools.licensing.keygen generate-keypair --out private_key.pem

    # Für jeden Kunden: einen signierten Lizenzschlüssel ausstellen.
    python -m tools.licensing.keygen issue --tier pro --customer "Max Mustermann" \\
        --private-key private_key.pem

    # Unbegrenzte Anzahl Ladestationen statt Tier-Standard:
    python -m tools.licensing.keygen issue --tier pro --max-stations 0 \\
        --private-key private_key.pem
"""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

KEY_PREFIX = "VLTB1"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def cmd_generate_keypair(args: argparse.Namespace) -> None:
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    with open(args.out, "wb") as f:
        f.write(pem)

    public_bytes = private_key.public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw
    )
    print(f"Privater Schlüssel geschrieben nach: {args.out}")
    print("NICHT committen, sicher aufbewahren (z. B. Passwort-Manager/Tresor).")
    print()
    print("Öffentlicher Schlüssel (in app/licensing.py::PUBLIC_KEY_B64 eintragen):")
    print(_b64url(public_bytes))


def cmd_issue(args: argparse.Namespace) -> None:
    with open(args.private_key, "rb") as f:
        private_key = load_pem_private_key(f.read(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        print("Fehler: Datei enthält keinen Ed25519-Privatschlüssel.", file=sys.stderr)
        sys.exit(1)

    max_stations = None if args.max_stations in (None, 0) else args.max_stations
    payload = {
        "tier": args.tier,
        "max_stations": max_stations,
        "issued_to": args.customer,
        "iat": datetime.date.today().isoformat(),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = private_key.sign(payload_bytes)

    key = f"{KEY_PREFIX}.{_b64url(payload_bytes)}.{_b64url(signature)}"
    print(key)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate-keypair", help="Neues Ed25519-Schlüsselpaar erzeugen")
    p_gen.add_argument("--out", required=True, help="Zieldatei für den privaten Schlüssel (PEM)")
    p_gen.set_defaults(func=cmd_generate_keypair)

    p_issue = sub.add_parser("issue", help="Signierten Lizenzschlüssel ausstellen")
    p_issue.add_argument("--tier", required=True, choices=["free", "pro", "enterprise"])
    p_issue.add_argument("--customer", default=None, help="Optionales Label (Kundenname)")
    p_issue.add_argument(
        "--max-stations", type=int, default=None,
        help="Explizite Obergrenze (0 = unbegrenzt). Ohne Angabe gilt der Tier-Standard.",
    )
    p_issue.add_argument("--private-key", required=True, help="Pfad zur privaten Schlüsseldatei (PEM)")
    p_issue.set_defaults(func=cmd_issue)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
