"""General filesystem helpers."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any


def ensure_directory(path: Path) -> Path:
    """Create a directory if it does not exist and return it."""

    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(value: str) -> str:
    """Convert an external identifier into a safe file name fragment."""

    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


async def write_json(path: Path, payload: Any) -> None:
    """Write JSON with deterministic formatting without blocking the event loop."""

    ensure_directory(path.parent)
    await asyncio.to_thread(_write_json_sync, path, payload)


def _write_json_sync(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)


def read_json(path: Path) -> Any:
    """Read a JSON file."""

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)
