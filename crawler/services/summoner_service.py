"""Summoner ingestion service."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from crawler.api.riot_client import RiotClient
from crawler.schemas.models import SummonerDTO
from crawler.utils.helpers import safe_filename, write_json


LOGGER = logging.getLogger(__name__)


class SummonerService:
    """Fetch, validate, and persist summoner data."""

    def __init__(self, client: RiotClient, raw_dir: Path) -> None:
        self.client = client
        self.raw_dir = raw_dir

    async def get_by_puuid(self, platform_region: str, puuid: str) -> SummonerDTO | None:
        """Fetch a summoner by PUUID and save the exact raw response."""

        try:
            raw = await self.client.get_summoner_by_puuid(platform_region, puuid)
            await self._save_raw(platform_region, puuid, raw)
            return SummonerDTO.model_validate(raw)
        except ValidationError:
            LOGGER.exception("Invalid summoner payload for puuid=%s", puuid)
            return None
        except Exception:
            LOGGER.exception("Failed to fetch summoner for puuid=%s", puuid)
            return None

    async def get_by_summoner_id(self, platform_region: str, encrypted_summoner_id: str) -> SummonerDTO | None:
        """Fetch a summoner by encrypted summoner ID and save the raw response."""

        try:
            raw = await self.client.get_summoner_by_id(platform_region, encrypted_summoner_id)
            puuid = str(raw.get("puuid", encrypted_summoner_id))
            await self._save_raw(platform_region, puuid, raw)
            return SummonerDTO.model_validate(raw)
        except ValidationError:
            LOGGER.exception("Invalid summoner payload for summoner_id=%s", encrypted_summoner_id)
            return None
        except Exception:
            LOGGER.exception("Failed to fetch summoner for summoner_id=%s", encrypted_summoner_id)
            return None

    async def _save_raw(self, platform_region: str, identifier: str, payload: dict[str, Any]) -> None:
        file_name = f"{safe_filename(platform_region)}_{safe_filename(identifier)}.json"
        await write_json(self.raw_dir / file_name, payload)
