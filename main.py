"""Command-line entrypoint for Riot data crawling and CSV processing."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import ValidationError

from crawler.api.riot_client import RiotClient
from crawler.config.settings import Settings, get_settings
from crawler.schemas.models import LeagueEntryDTO, MatchDTO, TimelineDTO
from crawler.services.match_service import MatchService
from crawler.services.ranked_service import RankedService
from crawler.services.summoner_service import SummonerService
from crawler.services.timeline_service import TimelineService
from crawler.utils.checkpoint import CheckpointManager
from crawler.utils.helpers import ensure_directory, read_json
from crawler.utils.logger import setup_logging


LOGGER = logging.getLogger(__name__)

PLAYER_COLUMNS = [
    "match_id",
    "participant_id",
    "puuid",
    "summoner_name",
    "riot_id_game_name",
    "riot_id_tagline",
    "champion_id",
    "champion_name",
    "kills",
    "deaths",
    "assists",
    "kda",
    "damage_dealt",
    "gold_earned",
    "vision_score",
    "win",
    "team_id",
    "position",
    "team_position",
    "individual_position",
]

MATCH_COLUMNS = [
    "match_id",
    "game_creation",
    "game_start_timestamp",
    "game_end_timestamp",
    "game_duration",
    "queue_id",
    "game_version",
    "map_id",
    "platform_id",
    "game_mode",
    "game_type",
    "winning_team",
    "participant_count",
]

RANKED_COLUMNS = [
    "puuid",
    "summoner_id",
    "queue_type",
    "tier",
    "rank",
    "league_points",
    "wins",
    "losses",
    "win_rate",
    "veteran",
    "inactive",
    "fresh_blood",
    "hot_streak",
]

TIMELINE_COLUMNS = [
    "match_id",
    "timestamp",
    "participant_id",
    "event_type",
    "event_category",
    "events",
    "related_participant_id",
    "position_x",
    "position_y",
    "item_id",
    "skill_slot",
    "monster_type",
    "monster_sub_type",
    "building_type",
    "lane_type",
    "ward_type",
    "killer_id",
    "victim_id",
    "creator_id",
    "assisting_participant_ids",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="riot-data-crawler",
        description="Lightweight async Riot Games crawler for raw JSON and analytics-ready CSVs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    crawl_puuids = subparsers.add_parser("crawl-puuids", help="Crawl data for one or more PUUIDs.")
    crawl_puuids.add_argument("--puuids", nargs="+", required=True, help="One or more Riot PUUIDs.")
    crawl_puuids.add_argument("--platform-region", default=None, help="Platform shard such as vn2, kr, euw1, na1.")
    crawl_puuids.add_argument("--routing-region", default=None, help="Match-V5 route such as sea, asia, europe, americas.")
    crawl_puuids.add_argument("--match-count", type=int, default=None, help="Number of match IDs to request per PUUID.")
    crawl_puuids.add_argument("--start", type=int, default=0, help="Match history offset.")
    crawl_puuids.add_argument("--queue", type=int, default=None, help="Optional Riot queue ID filter.")
    crawl_puuids.add_argument("--skip-ranked", action="store_true", help="Skip ranked endpoint collection.")
    crawl_puuids.add_argument("--skip-timelines", action="store_true", help="Skip timeline collection.")

    leaderboard = subparsers.add_parser("crawl-leaderboard", help="Crawl from ranked leaderboard entries.")
    leaderboard.add_argument("--platform-region", default=None, help="Platform shard such as vn2, kr, euw1, na1.")
    leaderboard.add_argument("--routing-region", default=None, help="Match-V5 route such as sea, asia, europe, americas.")
    leaderboard.add_argument("--queue", default="RANKED_SOLO_5x5", help="Ranked queue type.")
    leaderboard.add_argument("--tier", default="CHALLENGER", help="CHALLENGER, GRANDMASTER, MASTER, DIAMOND, etc.")
    leaderboard.add_argument("--division", default="I", help="Division for non apex tiers.")
    leaderboard.add_argument("--limit", type=int, default=25, help="Maximum leaderboard players to resolve.")
    leaderboard.add_argument("--match-count", type=int, default=None, help="Number of matches to crawl per player.")
    leaderboard.add_argument("--skip-timelines", action="store_true", help="Skip timeline collection.")

    subparsers.add_parser("process", help="Build processed CSVs from raw JSON files.")
    return parser.parse_args()


async def main_async() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    ensure_project_directories(settings)

    args = parse_args()
    if args.command == "process":
        process_raw_to_csv(settings.output_dir)
        return

    async with RiotClient(settings) as client:
        summoner_service = SummonerService(client, settings.output_dir / "raw" / "summoners")
        checkpoint_manager = CheckpointManager(
            settings.checkpoint_dir,
            settings.output_dir / "raw" / "matches",
            settings.output_dir / "raw" / "timelines",
        )
        await checkpoint_manager.hydrate_from_raw_files()
        match_service = MatchService(client, settings.output_dir / "raw" / "matches", checkpoint_manager)
        timeline_service = TimelineService(
            client,
            settings.output_dir / "raw" / "timelines",
            checkpoint_manager,
            extra_delay_seconds=settings.timeline_extra_delay_seconds,
        )
        ranked_service = RankedService(client, settings.output_dir / "raw" / "ranked", summoner_service)

        if args.command == "crawl-puuids":
            await crawl_puuids(settings, args, summoner_service, ranked_service, match_service, timeline_service)
        elif args.command == "crawl-leaderboard":
            await crawl_leaderboard(settings, args, ranked_service, match_service, timeline_service)
        else:
            raise ValueError(f"Unsupported command: {args.command}")

    process_raw_to_csv(settings.output_dir)


async def crawl_puuids(
    settings: Settings,
    args: argparse.Namespace,
    summoner_service: SummonerService,
    ranked_service: RankedService,
    match_service: MatchService,
    timeline_service: TimelineService,
) -> None:
    platform_region = (args.platform_region or settings.default_platform_region).lower()
    routing_region = (args.routing_region or settings.routing_region_for_platform(platform_region)).lower()
    match_count = args.match_count or settings.default_match_count

    for puuid in args.puuids:
        LOGGER.info("Crawling puuid=%s platform=%s route=%s", puuid, platform_region, routing_region)
        summoner = await summoner_service.get_by_puuid(platform_region, puuid)
        if summoner and not args.skip_ranked:
            await ranked_service.get_entries_for_summoner(platform_region, summoner)

        matches = await match_service.crawl_matches_for_puuid(
            routing_region,
            puuid,
            start=args.start,
            count=match_count,
            queue=args.queue,
        )
        if args.skip_timelines:
            continue
        for match in matches:
            await timeline_service.crawl_timeline(routing_region, match.metadata.match_id)


async def crawl_leaderboard(
    settings: Settings,
    args: argparse.Namespace,
    ranked_service: RankedService,
    match_service: MatchService,
    timeline_service: TimelineService,
) -> None:
    platform_region = (args.platform_region or settings.default_platform_region).lower()
    routing_region = (args.routing_region or settings.routing_region_for_platform(platform_region)).lower()
    match_count = args.match_count or settings.default_match_count
    entries = await ranked_service.crawl_leaderboard(
        platform_region,
        queue=args.queue,
        tier=args.tier,
        division=args.division,
        limit=args.limit,
    )
    puuids = sorted({entry.puuid for entry in entries if entry.puuid})
    LOGGER.info("Resolved %s leaderboard PUUIDs", len(puuids))
    for puuid in puuids:
        matches = await match_service.crawl_matches_for_puuid(routing_region, puuid, count=match_count)
        if args.skip_timelines:
            continue
        for match in matches:
            await timeline_service.crawl_timeline(routing_region, match.metadata.match_id)


def ensure_project_directories(settings: Settings) -> None:
    """Create all expected data directories."""

    for path in [
        settings.output_dir / "raw" / "summoners",
        settings.output_dir / "raw" / "matches",
        settings.output_dir / "raw" / "timelines",
        settings.output_dir / "raw" / "ranked",
        settings.output_dir / "processed",
        settings.checkpoint_dir,
    ]:
        ensure_directory(path)


def process_raw_to_csv(output_dir: Path) -> None:
    """Build all processed CSVs from current raw JSON files."""

    processed_dir = ensure_directory(output_dir / "processed")
    summoner_payloads = load_json_payloads(output_dir / "raw" / "summoners")
    match_payloads = load_json_payloads(output_dir / "raw" / "matches")
    timeline_payloads = load_json_payloads(output_dir / "raw" / "timelines")
    ranked_payloads = load_json_payloads(output_dir / "raw" / "ranked")
    summoner_id_to_puuid = build_summoner_id_to_puuid(summoner_payloads)

    players: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    for _, payload in match_payloads:
        try:
            match = MatchDTO.model_validate(payload)
        except ValidationError:
            LOGGER.exception("Skipping corrupted match payload")
            continue
        matches.append(MatchService.to_match_record(match))
        players.extend(MatchService.to_player_records(match))

    ranked_records: list[dict[str, Any]] = []
    for file_path, payload in ranked_payloads:
        for item in iter_ranked_items(file_path, payload, summoner_id_to_puuid):
            try:
                ranked_records.append(RankedService.to_processed_record(LeagueEntryDTO.model_validate(item)))
            except ValidationError:
                LOGGER.debug("Skipping non-entry ranked payload item", exc_info=True)

    timeline_records: list[dict[str, Any]] = []
    for _, payload in timeline_payloads:
        try:
            timeline_records.extend(TimelineService.to_event_records(TimelineDTO.model_validate(payload)))
        except ValidationError:
            LOGGER.exception("Skipping corrupted timeline payload")

    write_csv(processed_dir / "players.csv", players, PLAYER_COLUMNS, subset=["match_id", "puuid"])
    write_csv(processed_dir / "matches.csv", matches, MATCH_COLUMNS, subset=["match_id"])
    write_csv(processed_dir / "ranked.csv", ranked_records, RANKED_COLUMNS, subset=["puuid", "summoner_id", "queue_type"])
    write_csv(
        processed_dir / "timelines.csv",
        timeline_records,
        TIMELINE_COLUMNS,
        subset=TIMELINE_COLUMNS,
    )
    LOGGER.info("Processed CSVs written to %s", processed_dir)


def load_json_payloads(path: Path) -> list[tuple[Path, Any]]:
    """Load every JSON file in a directory, skipping corrupted files."""

    if not path.exists():
        return []
    payloads: list[tuple[Path, Any]] = []
    for file_path in sorted(path.glob("*.json")):
        try:
            payloads.append((file_path, read_json(file_path)))
        except Exception:
            LOGGER.exception("Skipping unreadable JSON file: %s", file_path)
    return payloads


def build_summoner_id_to_puuid(summoner_payloads: list[tuple[Path, Any]]) -> dict[str, str]:
    """Build a lookup from encrypted summoner ID to PUUID from raw summoner files."""

    mapping: dict[str, str] = {}
    for _, payload in summoner_payloads:
        if not isinstance(payload, dict):
            continue
        summoner_id = payload.get("id")
        puuid = payload.get("puuid")
        if summoner_id and puuid:
            mapping[str(summoner_id)] = str(puuid)
    return mapping


def iter_ranked_items(
    file_path: Path,
    payload: Any,
    summoner_id_to_puuid: dict[str, str],
) -> list[dict[str, Any]]:
    """Yield ranked entries enriched for processing while raw files stay exact."""

    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        context = {
            "leagueId": payload.get("leagueId"),
            "queueType": payload.get("queue"),
            "tier": payload.get("tier"),
        }
        items = [dict(context, **item) for item in payload["entries"]]
    elif isinstance(payload, list):
        items = [dict(item) for item in payload if isinstance(item, dict)]
    else:
        return []

    enriched: list[dict[str, Any]] = []
    for item in items:
        summoner_id = item.get("summonerId")
        puuid = item.get("puuid")
        if not puuid and summoner_id:
            puuid = summoner_id_to_puuid.get(str(summoner_id))
        if not puuid and not file_path.stem.startswith("leaderboard_"):
            puuid = file_path.stem
        enriched.append(dict(item, puuid=puuid))
    return enriched


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str], subset: list[str]) -> None:
    """Write deterministic CSV output with headers even when there are no rows."""

    frame = pd.DataFrame(rows)
    if frame.empty:
        frame = pd.DataFrame(columns=columns)
    else:
        frame = frame.reindex(columns=columns)
        frame = frame.drop_duplicates(subset=[col for col in subset if col in frame.columns])
        sort_columns = [col for col in subset if col in frame.columns]
        if sort_columns:
            frame = frame.sort_values(sort_columns)
    ensure_directory(path.parent)
    frame.to_csv(path, index=False)


if __name__ == "__main__":
    asyncio.run(main_async())
