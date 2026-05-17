"""Checkpoint management for idempotent daily Riot crawls."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crawler.utils.helpers import ensure_directory, safe_filename


LOGGER = logging.getLogger(__name__)


class CheckpointManager:
    """JSON-backed checkpoint manager for processed Riot match IDs.

    The manager is deliberately simple and durable:
    - it stores processed match IDs in `checkpoints/matches.json`
    - it stores processed timeline IDs in `checkpoints/timelines.json`
    - writes are atomic through a temporary file and replace
    - existing raw JSON files can hydrate checkpoints after a fresh clone or key rotation
    """

    def __init__(self, checkpoint_dir: Path, match_raw_dir: Path, timeline_raw_dir: Path | None = None) -> None:
        self.checkpoint_dir = ensure_directory(checkpoint_dir)
        self.match_raw_dir = ensure_directory(match_raw_dir)
        self.timeline_raw_dir = ensure_directory(timeline_raw_dir) if timeline_raw_dir else None
        self._lock = asyncio.Lock()

    async def hydrate_from_raw_files(self) -> None:
        """Add already existing raw match/timeline files to checkpoints."""

        async with self._lock:
            match_ids = self._raw_ids(self.match_raw_dir)
            if match_ids:
                payload = self._load_payload(self._matches_path())
                items = self._get_items(payload)
                before = len(items)
                items.update(match_ids)
                self._set_items(payload, items)
                self._save_payload(self._matches_path(), payload)
                LOGGER.info("Hydrated match checkpoint from raw files count=%s added=%s", len(match_ids), len(items) - before)

            if self.timeline_raw_dir is not None:
                timeline_ids = self._raw_ids(self.timeline_raw_dir)
                if timeline_ids:
                    payload = self._load_payload(self._timelines_path())
                    items = self._get_items(payload)
                    before = len(items)
                    items.update(timeline_ids)
                    self._set_items(payload, items)
                    self._save_payload(self._timelines_path(), payload)
                    LOGGER.info(
                        "Hydrated timeline checkpoint from raw files count=%s added=%s",
                        len(timeline_ids),
                        len(items) - before,
                    )

    async def has_match(self, match_id: str) -> bool:
        """Return true when a match has already been processed."""

        async with self._lock:
            payload = self._load_payload(self._matches_path())
            return match_id in self._get_items(payload)

    async def should_skip_match(self, match_id: str) -> bool:
        """Return true when no match-detail API call should be made."""

        raw_path = self.match_raw_path(match_id)
        async with self._lock:
            payload = self._load_payload(self._matches_path())
            items = self._get_items(payload)
            if raw_path.exists():
                if match_id not in items:
                    items.add(match_id)
                    self._set_items(payload, items)
                    self._save_payload(self._matches_path(), payload)
                LOGGER.info("Skipping match_id=%s because raw match JSON already exists.", match_id)
                return True
            if match_id in items:
                LOGGER.info("Skipping match_id=%s because it is already in checkpoint.", match_id)
                return True
            return False

    async def mark_match_processed(self, match_id: str) -> None:
        """Persist one processed match ID immediately after a successful raw save."""

        async with self._lock:
            payload = self._load_payload(self._matches_path())
            items = self._get_items(payload)
            items.add(match_id)
            self._set_items(payload, items)
            self._save_payload(self._matches_path(), payload)

    async def should_skip_timeline(self, match_id: str) -> bool:
        """Return true when no timeline API call should be made."""

        if self.timeline_raw_dir is None:
            return False
        raw_path = self.timeline_raw_path(match_id)
        async with self._lock:
            payload = self._load_payload(self._timelines_path())
            items = self._get_items(payload)
            if raw_path.exists():
                if match_id not in items:
                    items.add(match_id)
                    self._set_items(payload, items)
                    self._save_payload(self._timelines_path(), payload)
                LOGGER.info("Skipping timeline match_id=%s because raw timeline JSON already exists.", match_id)
                return True
            if match_id in items:
                LOGGER.info("Skipping timeline match_id=%s because it is already in checkpoint.", match_id)
                return True
            return False

    async def mark_timeline_processed(self, match_id: str) -> None:
        """Persist one processed timeline ID immediately after a successful raw save."""

        async with self._lock:
            payload = self._load_payload(self._timelines_path())
            items = self._get_items(payload)
            items.add(match_id)
            self._set_items(payload, items)
            self._save_payload(self._timelines_path(), payload)

    def match_raw_path(self, match_id: str) -> Path:
        """Return the raw match JSON path for a match ID."""

        return self.match_raw_dir / f"{safe_filename(match_id)}.json"

    def timeline_raw_path(self, match_id: str) -> Path:
        """Return the raw timeline JSON path for a match ID."""

        if self.timeline_raw_dir is None:
            raise RuntimeError("Timeline raw directory is not configured.")
        return self.timeline_raw_dir / f"{safe_filename(match_id)}.json"

    def _matches_path(self) -> Path:
        return self.checkpoint_dir / "matches.json"

    def _timelines_path(self) -> Path:
        return self.checkpoint_dir / "timelines.json"

    @staticmethod
    def _raw_ids(raw_dir: Path) -> set[str]:
        return {path.stem for path in raw_dir.glob("*.json")}

    @staticmethod
    def _default_payload() -> dict[str, Any]:
        return {
            "version": 1,
            "updated_at": None,
            "processed_match_ids": [],
        }

    def _load_payload(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return self._default_payload()
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (json.JSONDecodeError, OSError):
            LOGGER.exception("Checkpoint file is unreadable; starting with empty checkpoint path=%s", path)
            return self._default_payload()

        if isinstance(data, dict):
            if "processed_match_ids" not in data:
                data["processed_match_ids"] = data.get("items", [])
            return data
        if isinstance(data, list):
            payload = self._default_payload()
            payload["processed_match_ids"] = [str(item) for item in data]
            return payload
        return self._default_payload()

    @staticmethod
    def _get_items(payload: dict[str, Any]) -> set[str]:
        items = payload.setdefault("processed_match_ids", [])
        if not isinstance(items, list):
            items = []
            payload["processed_match_ids"] = items
        return {str(item) for item in items}

    @staticmethod
    def _set_items(payload: dict[str, Any], items: set[str]) -> None:
        payload["processed_match_ids"] = sorted(items)

    def _save_payload(self, path: Path, payload: dict[str, Any]) -> None:
        items = sorted(self._get_items(payload))
        payload["processed_match_ids"] = items
        payload["total"] = len(items)
        payload["updated_at"] = datetime.now(UTC).isoformat()
        ensure_directory(path.parent)
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
        tmp_path.replace(path)
