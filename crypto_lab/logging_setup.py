"""Structured, rotating-friendly logging setup."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


DEFAULT_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)
DEFAULT_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    max_bytes: int = 5_000_000,
    backup_count: int = 3,
) -> logging.Logger:
    """Configure root logger with console and optional rotating file handler."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(DEFAULT_FORMAT, datefmt=DEFAULT_DATEFMT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    return logging.getLogger("crypto_lab")

