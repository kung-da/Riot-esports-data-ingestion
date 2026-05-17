"""Ranked data ingestion and transformation service."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from crawler.api.riot_client import RiotClient
from crawler.schemas.models import LeagueEntryDTO, SummonerDTO
from crawler.services.summoner_service import SummonerService
from crawler.utils.helpers import safe_filename, write_json


LOGGER = logging.getLogger(__name__)


class RankedService:
    """Fetch ranked entries from summoner profiles or leaderboard endpoints."""

    def __init__(self, client: RiotClient, raw_dir: Path, summoner_service: SummonerService) -> None:
        self.client = client
        self.raw_dir = raw_dir
        self.summoner_service = summoner_service

    async def get_entries_for_summoner(
        self,
        platform_region: str,
        summoner: SummonerDTO,
    ) -> list[LeagueEntryDTO]:
        """Fetch ranked entries for a single summoner."""

        if not summoner.id:
            LOGGER.info(
                "Skipping ranked lookup for puuid=%s because Riot summoner payload did not include encrypted summoner id.",
                summoner.puuid,
            )
            return []

        try:
            raw = await self.client.get_ranked_entries_by_summoner_id(platform_region, summoner.id)
            await write_json(self.raw_dir / f"{safe_filename(summoner.puuid)}.json", raw)
            enriched = [dict(entry, puuid=summoner.puuid) for entry in raw]
            return [LeagueEntryDTO.model_validate(entry) for entry in enriched]
        except ValidationError:
            LOGGER.exception("Invalid ranked payload for puuid=%s", summoner.puuid)
            return []
        except Exception:
            LOGGER.exception("Failed to fetch ranked entries for puuid=%s", summoner.puuid)
            return []

    async def crawl_leaderboard(
        self,
        platform_region: str,
        *,
        queue: str = "RANKED_SOLO_5x5",
        tier: str = "CHALLENGER",
        division: str = "I",
        limit: int = 100,
    ) -> list[LeagueEntryDTO]:
        """Fetch a ranked leaderboard and resolve entries to PUUID when possible."""

        tier_upper = tier.upper()
        if tier_upper in {"CHALLENGER", "GRANDMASTER", "MASTER"}:
            payload = await self.client.get_high_tier_league(platform_region, queue=queue, tier=tier_upper)
            raw_entries = payload.get("entries", [])
            entry_context = {
                "leagueId": payload.get("leagueId"),
                "queueType": payload.get("queue", queue),
                "tier": payload.get("tier", tier_upper),
            }
            await write_json(
                self.raw_dir / f"leaderboard_{safe_filename(platform_region)}_{tier_upper.lower()}_{safe_filename(queue)}.json",
                payload,
            )
        else:
            raw_entries = await self.client.get_league_entries(
                platform_region,
                queue=queue,
                tier=tier_upper,
                division=division.upper(),
                page=1,
            )
            entry_context = {"queueType": queue, "tier": tier_upper}
            await write_json(
                self.raw_dir
                / f"leaderboard_{safe_filename(platform_region)}_{tier_upper.lower()}_{division.upper()}_{safe_filename(queue)}.json",
                raw_entries,
            )

        entries: list[LeagueEntryDTO] = []
        for raw_entry in raw_entries[:limit]:
            summoner_id = raw_entry.get("summonerId")
            puuid = raw_entry.get("puuid")
            if not puuid and summoner_id:
                summoner = await self.summoner_service.get_by_summoner_id(platform_region, summoner_id)
                puuid = summoner.puuid if summoner else None

            enriched = dict(entry_context, **raw_entry, puuid=puuid)
            try:
                entries.append(LeagueEntryDTO.model_validate(enriched))
            except ValidationError:
                LOGGER.exception("Skipping invalid leaderboard entry for summoner_id=%s", summoner_id)
        return entries

    @staticmethod
    def to_processed_record(entry: LeagueEntryDTO) -> dict[str, Any]:
        """Flatten one ranked entry for analytics-ready CSV output."""

        games = entry.wins + entry.losses
        win_rate = round(entry.wins / games, 4) if games else None
        return {
            "puuid": entry.puuid,
            "summoner_id": entry.summoner_id,
            "queue_type": entry.queue_type,
            "tier": entry.tier,
            "rank": entry.rank,
            "league_points": entry.league_points,
            "wins": entry.wins,
            "losses": entry.losses,
            "win_rate": win_rate,
            "veteran": entry.veteran,
            "inactive": entry.inactive,
            "fresh_blood": entry.fresh_blood,
            "hot_streak": entry.hot_streak,
        }
