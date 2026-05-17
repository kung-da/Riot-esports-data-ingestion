"""Match ingestion and transformation service."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from crawler.api.riot_client import RiotClient
from crawler.schemas.models import MatchDTO
from crawler.utils.checkpoint import CheckpointManager
from crawler.utils.helpers import write_json


LOGGER = logging.getLogger(__name__)


class MatchService:
    """Fetch Match-V5 histories and save only new match detail payloads."""

    def __init__(self, client: RiotClient, raw_dir: Path, checkpoint_manager: CheckpointManager) -> None:
        self.client = client
        self.raw_dir = raw_dir
        self.checkpoint_manager = checkpoint_manager

    async def get_match_ids_for_puuid(
        self,
        routing_region: str,
        puuid: str,
        *,
        start: int = 0,
        count: int = 20,
        queue: int | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        match_type: str | None = None,
    ) -> list[str]:
        """Fetch recent match IDs for a PUUID."""

        try:
            return await self.client.get_match_ids_by_puuid(
                routing_region,
                puuid,
                start=start,
                count=count,
                queue=queue,
                start_time=start_time,
                end_time=end_time,
                match_type=match_type,
            )
        except Exception:
            LOGGER.exception("Failed to fetch match ids for puuid=%s", puuid)
            return []

    async def crawl_new_match(self, routing_region: str, match_id: str) -> MatchDTO | None:
        """Fetch one match only if neither raw JSON nor checkpoint already exists."""

        if await self.checkpoint_manager.should_skip_match(match_id):
            return None

        raw_path = self.checkpoint_manager.match_raw_path(match_id)
        try:
            raw = await self.client.get_match(routing_region, match_id)
            await write_json(raw_path, raw)
            match = MatchDTO.model_validate(raw)
            await self.checkpoint_manager.mark_match_processed(match_id)
            LOGGER.info("Saved new match match_id=%s", match_id)
            return match
        except ValidationError:
            LOGGER.exception("Invalid match payload for match_id=%s; raw file was saved for inspection.", match_id)
            return None
        except Exception:
            LOGGER.exception("Failed to fetch match_id=%s", match_id)
            return None

    async def crawl_matches_for_puuid(
        self,
        routing_region: str,
        puuid: str,
        *,
        start: int = 0,
        count: int = 20,
        queue: int | None = None,
    ) -> list[MatchDTO]:
        """Incrementally crawl new matches for a player using checkpoint guards."""

        match_ids = await self.get_match_ids_for_puuid(
            routing_region,
            puuid,
            start=start,
            count=count,
            queue=queue,
        )
        results: list[MatchDTO] = []
        for match_id in match_ids:
            match = await self.crawl_new_match(routing_region, match_id)
            if match is not None:
                results.append(match)
        LOGGER.info("Crawled new matches for puuid=%s count=%s", puuid, len(results))
        return results

    @staticmethod
    def to_match_record(match: MatchDTO) -> dict[str, Any]:
        """Flatten match metadata into one analytics row."""

        winning_team = next((team.team_id for team in match.info.teams if team.win), None)
        return {
            "match_id": match.metadata.match_id,
            "game_creation": match.info.game_creation,
            "game_start_timestamp": match.info.game_start_timestamp,
            "game_end_timestamp": match.info.game_end_timestamp,
            "game_duration": match.info.game_duration,
            "queue_id": match.info.queue_id,
            "game_version": match.info.game_version,
            "map_id": match.info.map_id,
            "platform_id": match.info.platform_id,
            "game_mode": match.info.game_mode,
            "game_type": match.info.game_type,
            "winning_team": winning_team,
            "participant_count": len(match.info.participants),
        }

    @staticmethod
    def to_player_records(match: MatchDTO) -> list[dict[str, Any]]:
        """Flatten participant stats into ML-friendly player rows."""

        records: list[dict[str, Any]] = []
        for participant in match.info.participants:
            deaths = participant.deaths
            kda = participant.kills + participant.assists if deaths == 0 else (participant.kills + participant.assists) / deaths
            summoner_name = participant.summoner_name or participant.riot_id_game_name
            records.append(
                {
                    "match_id": match.metadata.match_id,
                    "participant_id": participant.participant_id,
                    "puuid": participant.puuid,
                    "summoner_name": summoner_name,
                    "riot_id_game_name": participant.riot_id_game_name,
                    "riot_id_tagline": participant.riot_id_tagline,
                    "champion_id": participant.champion_id,
                    "champion_name": participant.champion_name,
                    "kills": participant.kills,
                    "deaths": participant.deaths,
                    "assists": participant.assists,
                    "kda": round(float(kda), 4),
                    "damage_dealt": participant.total_damage_dealt_to_champions,
                    "gold_earned": participant.gold_earned,
                    "vision_score": participant.vision_score,
                    "win": participant.win,
                    "team_id": participant.team_id,
                    "position": participant.team_position or participant.individual_position,
                    "team_position": participant.team_position,
                    "individual_position": participant.individual_position,
                }
            )
        return records
