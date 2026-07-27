"""Logging setup for NRC Social Agent.

Logs go to stdout so they're captured uniformly by JustRunMy.App (or any
container runtime) without any file-handling of our own.
"""

from __future__ import annotations

import logging
import sys


def configure_logging(log_level: str) -> None:
    level = logging.getLevelName(log_level.upper())
    if not isinstance(level, int):
        raise ValueError(f"Invalid LOG_LEVEL: {log_level!r}")

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
