"""Logging-Konfiguration: nach stdout/journald und optional in eine Datei."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config import settings


def setup_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Konsole/journald
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.handlers = [stream]

    # Optional zusätzlich in eine rotierende Datei
    if settings.log_file:
        fh = RotatingFileHandler(
            settings.log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

    # pymodbus ist sehr gesprächig -> anheben
    logging.getLogger("pymodbus").setLevel(logging.WARNING)
