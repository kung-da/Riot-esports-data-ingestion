"""Timeline ingestion and event-level transformation service."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from crawler.api.riot_client import RiotClient
from crawler.schemas.models import TimelineDTO
from crawler.utils.checkpoint import CheckpointManager
from crawler.utils.helpers import write_json


LOGGER = logging.getLogger(__name__)


class TimelineService:
    """Fetch Match-V5 timelines sequentially and transform timeline events."""

    def __init__(
        self,
        client: RiotClient,
        raw_dir: Path,
        checkpoint_manager: CheckpointManager,
        extra_delay_seconds: float = 2.0,
    ) -> None:
        self.client = client
        self.raw_dir = raw_dir
        self.checkpoint_manager = checkpoint_manager
        self.extra_delay_seconds = extra_delay_seconds

    async def crawl_timeline(self, routing_region: str, match_id: str) -> TimelineDTO | None:
        """Fetch one timeline if not already saved or checkpointed."""

        if await self.checkpoint_manager.should_skip_timeline(match_id):
            return None

        raw_path = self.checkpoint_manager.timeline_raw_path(match_id)
        try:
            if self.extra_delay_seconds > 0:
                await asyncio.sleep(self.extra_delay_seconds)
            raw = await self.client.get_timeline(routing_region, match_id)
            await write_json(raw_path, raw)
            timeline = TimelineDTO.model_validate(raw)
            await self.checkpoint_manager.mark_timeline_processed(match_id)
            LOGGER.info("Saved new timeline match_id=%s", match_id)
            return timeline
        except ValidationError:
            LOGGER.exception("Invalid timeline payload for match_id=%s; raw file was saved for inspection.", match_id)
            return None
        except Exception:
            LOGGER.exception("Failed to fetch timeline for match_id=%s", match_id)
            return None

    @staticmethod
    def to_event_records(timeline: TimelineDTO) -> list[dict[str, Any]]:
        """Convert Riot frame events into normalized event rows."""

        match_id = timeline.metadata.match_id
        records: list[dict[str, Any]] = []
        for frame in timeline.info.frames:
            timestamp = frame.get("timestamp")
            for event in frame.get("events", []):
                records.extend(TimelineService._expand_event(match_id, timestamp, event))
        return records

    @staticmethod
    def _expand_event(match_id: str, timestamp: int | None, event: dict[str, Any]) -> list[dict[str, Any]]:
        event_type = str(event.get("type", "UNKNOWN"))
        position = event.get("position") or {}
        base = {
            "match_id": match_id,
            "timestamp": timestamp,
            "event_type": event_type,
            "position_x": position.get("x"),
            "position_y": position.get("y"),
            "item_id": event.get("itemId"),
            "skill_slot": event.get("skillSlot"),
            "monster_type": event.get("monsterType"),
            "monster_sub_type": event.get("monsterSubType"),
            "building_type": event.get("buildingType"),
            "lane_type": event.get("laneType"),
            "ward_type": event.get("wardType"),
            "killer_id": event.get("killerId"),
            "victim_id": event.get("victimId"),
            "creator_id": event.get("creatorId"),
            "assisting_participant_ids": "|".join(str(item) for item in event.get("assistingParticipantIds", [])),
        }

        if event_type == "CHAMPION_KILL":
            records = []
            killer_id = event.get("killerId")
            victim_id = event.get("victimId")
            if killer_id:
                records.append(
                    dict(base, participant_id=killer_id, event_category="kill", events="kill", related_participant_id=victim_id)
                )
            if victim_id:
                records.append(
                    dict(base, participant_id=victim_id, event_category="death", events="death", related_participant_id=killer_id)
                )
            for assister_id in event.get("assistingParticipantIds", []):
                records.append(
                    dict(base, participant_id=assister_id, event_category="assist", events="assist", related_participant_id=victim_id)
                )
            return records

        participant_id = (
            event.get("participantId")
            or event.get("killerId")
            or event.get("creatorId")
            or event.get("victimId")
            or event.get("recipientId")
        )
        category_by_type = {
            "WARD_PLACED": "ward_place",
            "WARD_KILL": "ward_kill",
            "ELITE_MONSTER_KILL": "objective",
            "BUILDING_KILL": "building",
            "ITEM_PURCHASED": "item_purchase",
            "ITEM_SOLD": "item_sell",
            "ITEM_DESTROYED": "item_destroy",
            "ITEM_UNDO": "item_undo",
            "SKILL_LEVEL_UP": "skill_level",
            "LEVEL_UP": "level_up",
            "TURRET_PLATE_DESTROYED": "plate_destroy",
        }
        event_category = category_by_type.get(event_type, event_type.lower())
        return [
            dict(
                base,
                participant_id=participant_id,
                event_category=event_category,
                events=event_category,
                related_participant_id=event.get("victimId") or event.get("killerId") or event.get("recipientId"),
            )
        ]
