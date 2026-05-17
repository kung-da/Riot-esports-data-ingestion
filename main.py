"""Command-line entrypoint for Riot data crawling and CSV processing."""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import time
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
    leaderboard.add_argument("--tier", default="ALL", help="ALL, CHALLENGER, GRANDMASTER, MASTER, DIAMOND, etc.")
    leaderboard.add_argument("--division", default="I", help="Division for non apex tiers.")
    leaderboard.add_argument("--limit", type=int, default=25, help="Maximum leaderboard players to resolve.")
    leaderboard.add_argument("--match-count", type=int, default=None, help="Number of matches to crawl per player.")
    leaderboard.add_argument("--skip-timelines", action="store_true", help="Skip timeline collection.")

    overnight = subparsers.add_parser("crawl-overnight", help="Run safe overnight VN2 crawler with batching.")
    overnight.add_argument("--hours", type=float, default=None, help="Maximum runtime in hours.")
    overnight.add_argument("--target-matches", type=int, default=None, help="Target new matches to save.")
    overnight.add_argument("--platform-region", default="vn2", help="Overnight mode is designed for vn2.")
    overnight.add_argument("--leaderboard-limit", type=int, default=None, help="Seed players per tier.")
    overnight.add_argument("--match-count", type=int, default=None, help="Match IDs requested per player page.")
    overnight.add_argument("--batch-size", type=int, default=None, help="New matches per batch before sleeping.")
    overnight.add_argument("--batch-sleep-min", type=int, default=None, help="Minimum batch sleep seconds.")
    overnight.add_argument("--batch-sleep-max", type=int, default=None, help="Maximum batch sleep seconds.")
    overnight.add_argument("--tiers", nargs="*", default=None, help="Tier plan such as CHALLENGER GRANDMASTER MASTER DIAMOND:I.")
    overnight.add_argument("--puuids", nargs="*", default=None, help="Optional seed PUUIDs instead of leaderboard discovery.")
    overnight.add_argument("--queue", default="RANKED_SOLO_5x5", help="Ranked queue for leaderboard discovery.")
    overnight.add_argument("--match-queue", type=int, default=None, help="Optional Riot match queue ID filter.")
    overnight.add_argument("--skip-timelines", action="store_true", help="Skip timeline crawling.")

    subparsers.add_parser("process", help="Build processed CSVs from raw JSON files.")
    return parser.parse_args()


async def main_async() -> None:
    settings = get_settings()
    args = parse_args()
    setup_logging(settings.log_level, settings.log_dir, args.command)
    ensure_project_directories(settings)

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
        elif args.command == "crawl-overnight":
            await crawl_overnight(settings, args, ranked_service, match_service, timeline_service, summoner_service)
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
    tier_upper = args.tier.upper()
    if tier_upper in {"ALL", "TOP"}:
        puuids = await collect_leaderboard_puuids(
            settings,
            ranked_service,
            platform_region=platform_region,
            queue=args.queue,
            tier_plan=settings.overnight_tier_plan,
            limit_per_tier=args.limit,
        )
    else:
        entries = await ranked_service.crawl_leaderboard(
            platform_region,
            queue=args.queue,
            tier=tier_upper,
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


async def crawl_overnight(
    settings: Settings,
    args: argparse.Namespace,
    ranked_service: RankedService,
    match_service: MatchService,
    timeline_service: TimelineService,
    summoner_service: SummonerService,
) -> None:
    """Run batched safe overnight crawling for VN2."""

    platform_region = (args.platform_region or "vn2").lower()
    if platform_region != "vn2":
        raise ValueError("crawl-overnight is intentionally restricted to VN2 for this project.")

    routing_region = settings.routing_region_for_platform(platform_region)
    hours = args.hours or settings.overnight_hours
    target_matches = args.target_matches or settings.overnight_target_matches
    leaderboard_limit = args.leaderboard_limit or settings.overnight_leaderboard_limit_per_tier
    match_count = args.match_count or settings.overnight_match_count_per_puuid
    batch_size = args.batch_size or settings.overnight_batch_size
    batch_sleep_min = args.batch_sleep_min or settings.overnight_batch_sleep_min_seconds
    batch_sleep_max = args.batch_sleep_max or settings.overnight_batch_sleep_max_seconds
    include_timelines = settings.overnight_include_timelines and not args.skip_timelines
    tier_plan = parse_tier_plan(args.tiers) if args.tiers else settings.overnight_tier_plan

    estimated_capacity = estimate_safe_match_capacity(
        hours=hours,
        requests_per_minute=settings.requests_per_minute,
        include_timelines=include_timelines,
    )
    if target_matches > estimated_capacity:
        LOGGER.warning(
            "Requested target_matches=%s exceeds estimated safe capacity=%s for %.1f hours at %s rpm with timelines=%s. "
            "Crawler will stop by time limit instead of forcing unsafe speed.",
            target_matches,
            estimated_capacity,
            hours,
            settings.requests_per_minute,
            include_timelines,
        )

    seed_puuids = sorted(set(args.puuids or []))
    if not seed_puuids:
        seed_puuids = await collect_leaderboard_puuids(
            settings,
            ranked_service,
            platform_region=platform_region,
            queue=args.queue,
            tier_plan=tier_plan,
            limit_per_tier=leaderboard_limit,
        )
    if not seed_puuids:
        LOGGER.error("No seed PUUIDs available for overnight crawl.")
        return

    LOGGER.info(
        "Overnight crawl started platform=%s route=%s seeds=%s hours=%.1f target_matches=%s batch_size=%s timelines=%s",
        platform_region,
        routing_region,
        len(seed_puuids),
        hours,
        target_matches,
        batch_size,
        include_timelines,
    )

    deadline = time.monotonic() + hours * 3600
    offsets = {puuid: 0 for puuid in seed_puuids}
    total_new_matches = 0
    total_timelines = 0
    batch_new_matches = 0
    seed_index = 0

    while time.monotonic() < deadline and total_new_matches < target_matches:
        puuid = seed_puuids[seed_index % len(seed_puuids)]
        seed_index += 1
        start = offsets[puuid]
        offsets[puuid] = start + match_count

        # Crawl summoner data cho mỗi seed PUUID
        await summoner_service.get_by_puuid(platform_region, puuid)

        match_ids = await match_service.get_match_ids_for_puuid(
            routing_region,
            puuid,
            start=start,
            count=match_count,
            queue=args.match_queue,
        )
        if not match_ids:
            LOGGER.info("No match ids returned for puuid=%s start=%s", puuid, start)
            continue

        for match_id in match_ids:
            if time.monotonic() >= deadline or total_new_matches >= target_matches:
                break

            match = await match_service.crawl_new_match(routing_region, match_id)
            if match is None:
                continue

            total_new_matches += 1
            batch_new_matches += 1

            if include_timelines:
                timeline = await timeline_service.crawl_timeline(routing_region, match.metadata.match_id)
                if timeline is not None:
                    total_timelines += 1

            if batch_new_matches >= batch_size:
                process_raw_to_csv(settings.output_dir)
                sleep_for = random.randint(batch_sleep_min, max(batch_sleep_min, batch_sleep_max))
                LOGGER.info(
                    "Batch complete new_matches=%s total_new_matches=%s total_timelines=%s. Sleeping %s seconds.",
                    batch_new_matches,
                    total_new_matches,
                    total_timelines,
                    sleep_for,
                )
                batch_new_matches = 0
                if time.monotonic() + sleep_for < deadline:
                    await asyncio.sleep(sleep_for)
                else:
                    LOGGER.info("Skipping batch sleep because deadline is near.")

    LOGGER.info(
        "Overnight crawl finished total_new_matches=%s total_timelines=%s elapsed_hours=%.2f",
        total_new_matches,
        total_timelines,
        hours - max(deadline - time.monotonic(), 0) / 3600,
    )


async def collect_leaderboard_puuids(
    settings: Settings,
    ranked_service: RankedService,
    *,
    platform_region: str,
    queue: str,
    tier_plan: list[tuple[str, str]],
    limit_per_tier: int,
) -> list[str]:
    """Collect unique PUUID seeds from top VN2 ranked tiers."""

    puuids: set[str] = set()
    for tier, division in tier_plan:
        entries = await ranked_service.crawl_leaderboard(
            platform_region,
            queue=queue,
            tier=tier,
            division=division,
            limit=limit_per_tier,
        )
        tier_puuids = {entry.puuid for entry in entries if entry.puuid}
        puuids.update(tier_puuids)
        LOGGER.info("Resolved leaderboard tier=%s division=%s puuids=%s", tier, division, len(tier_puuids))
    return sorted(puuids)


def parse_tier_plan(values: list[str]) -> list[tuple[str, str]]:
    """Parse CLI tier values such as DIAMOND:I into `(tier, division)` pairs."""

    plan: list[tuple[str, str]] = []
    for value in values:
        item = value.strip().upper()
        if not item:
            continue
        if ":" in item:
            tier, division = item.split(":", 1)
            plan.append((tier.strip(), division.strip() or "I"))
        else:
            plan.append((item, "I"))
    return plan


def estimate_safe_match_capacity(hours: float, requests_per_minute: int, include_timelines: bool) -> int:
    """Estimate safe full-data match capacity from configured request budget."""

    requests_per_match = 2 if include_timelines else 1
    overhead_factor = 0.9
    return int(hours * 60 * requests_per_minute * overhead_factor / requests_per_match)


def ensure_project_directories(settings: Settings) -> None:
    """Create all expected data directories."""

    for path in [
        settings.output_dir / "raw" / "summoners",
        settings.output_dir / "raw" / "matches",
        settings.output_dir / "raw" / "timelines",
        settings.output_dir / "raw" / "ranked",
        settings.output_dir / "processed",
        settings.checkpoint_dir,
        settings.log_dir,
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
