"""Logging setup for console and overnight log files."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from crawler.utils.helpers import ensure_directory


def setup_logging(level: str = "INFO", log_dir: Path | None = None, command: str = "crawler") -> Path | None:
    """Configure process-wide logging to console and optionally to file."""

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [console_handler]

    log_path: Path | None = None
    if log_dir is not None:
        ensure_directory(log_dir)
        safe_command = command.replace(" ", "_").replace("/", "_").replace("\\", "_")
        log_path = log_dir / f"{safe_command}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    logging.basicConfig(level=numeric_level, handlers=handlers, force=True)
    if log_path is not None:
        logging.getLogger(__name__).info("Logging to %s", log_path)
    return log_path
