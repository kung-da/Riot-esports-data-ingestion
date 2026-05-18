# Riot Esports Data Ingestion

![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)
![Async](https://img.shields.io/badge/async-asyncio-green)
![Region](https://img.shields.io/badge/region-VN2-red)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

A safe, incremental, async crawler for Riot Games esports data on the **VN2** server. Saves full raw Match Detail and Timeline JSON first, then rebuilds analytics-ready CSVs — without ever duplicating an API call.

---

## Table of Contents

- [Features](#features)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [crawl-puuids](#crawl-puuids)
  - [crawl-leaderboard](#crawl-leaderboard)
  - [crawl-overnight](#crawl-overnight)
  - [process](#process)
- [Architecture & Data Flow](#architecture--data-flow)
- [Capacity Planning](#capacity-planning)
- [Output Schema](#output-schema)
- [Incremental Guarantees](#incremental-guarantees)
- [Testing](#testing)
- [Contributing](#contributing)

---

## Features

- **Raw-first**: Full Match Detail and Timeline JSON are persisted before any processing
- **Incremental**: Checkpoint files and raw file presence checks prevent duplicate API calls on re-runs
- **Rate-limit safe**: Global + per-method windows, exponential backoff with jitter, circuit breaker, and preemptive slowdown from Riot response headers
- **Overnight mode**: Batched crawling with configurable sleep windows, designed for unattended overnight runs
- **Leaderboard seeding**: Auto-discovers top players from Challenger / Grandmaster / Master / Diamond leaderboards
- **Flexible CLI**: Four subcommands covering targeted PUUID crawls, leaderboard-driven crawls, overnight batching, and CSV rebuild
- **Deterministic CSV output**: Sorted, deduplicated, schema-pinned CSVs with headers even when empty

---

## Project Structure

```
.
├── checkpoints/                  # Checkpoint JSON — tracks saved matches & timelines
├── crawler/                      # Core package
│   ├── api/                      #   RiotClient (async HTTP, rate limiter)
│   ├── config/                   #   Settings (pydantic, .env)
│   ├── schemas/                  #   Pydantic models: MatchDTO, TimelineDTO, etc.
│   ├── services/                 #   MatchService, TimelineService, RankedService, SummonerService
│   └── utils/                    #   CheckpointManager, helpers, logger
├── logs/                         # Per-run log files: <command>_YYYYMMDD_HHMMSS.log
├── output/
│   ├── raw/
│   │   ├── summoners/
│   │   ├── matches/
│   │   ├── timelines/
│   │   └── ranked/
│   └── processed/
│       ├── players.csv
│       ├── matches.csv
│       ├── timelines.csv
│       └── ranked.csv
├── tests/
├── main.py                       # CLI entrypoint
├── overnight_crawl.ps1           # PowerShell wrapper for overnight mode
├── requirements.txt
├── pytest.ini
└── .env.example
```

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/kung-da/Riot-esports-data-ingestion.git
cd Riot-esports-data-ingestion

# 2. Create and activate a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy the example env file
copy .env.example .env       # Windows
cp .env.example .env         # macOS / Linux
```

Open `.env` and set your Riot API key:

```env
RIOT_API_KEY=RGAPI-your-current-daily-key
```

> **Key rotation**: To use a new daily key, replace only `RIOT_API_KEY`. All raw JSON files and checkpoints remain unchanged.

---

## Configuration

All settings are read from `.env` (or environment variables). The defaults below are tuned for safe overnight crawling with a Riot development key.

| Variable | Default | Description |
|---|---|---|
| `RIOT_API_KEY` | *(required)* | Your Riot Games API key |
| `MAX_CONCURRENCY` | `5` | Max simultaneous async requests |
| `REQUESTS_PER_MINUTE` | `20` | Global request cap per minute |
| `METHOD_REQUESTS_PER_MINUTE` | `12` | Per-method request cap per minute |
| `REQUEST_SLEEP_MIN_SECONDS` | `1.8` | Minimum sleep between requests |
| `REQUEST_SLEEP_MAX_SECONDS` | `3.5` | Maximum sleep between requests |
| `OVERNIGHT_BATCH_SIZE` | `1000` | New matches per batch before sleeping |
| `OVERNIGHT_BATCH_SLEEP_MIN_SECONDS` | `480` | Minimum inter-batch sleep (8 min) |
| `OVERNIGHT_BATCH_SLEEP_MAX_SECONDS` | `900` | Maximum inter-batch sleep (15 min) |
| `OVERNIGHT_INCLUDE_TIMELINES` | `true` | Whether to collect timelines overnight |

The rate limiter enforces **global**, **per-region**, and **per-method** windows simultaneously, and reads `X-Rate-Limit-Count` / `Retry-After` headers to preemptively slow down before hitting a 429.

---

## Usage

### crawl-puuids

Crawl match history and ranked data for one or more specific PUUIDs.

```bash
python main.py crawl-puuids \
  --puuids <PUUID_1> <PUUID_2> \
  --platform-region vn2 \
  --match-count 20

# Skip ranked and timeline collection
python main.py crawl-puuids \
  --puuids <PUUID_1> \
  --platform-region vn2 \
  --skip-ranked \
  --skip-timelines
```

| Flag | Default | Description |
|---|---|---|
| `--puuids` | *(required)* | One or more Riot PUUIDs |
| `--platform-region` | env default | Platform shard: `vn2`, `kr`, `euw1`, `na1` |
| `--routing-region` | auto | Match-V5 route: `sea`, `asia`, `europe`, `americas` |
| `--match-count` | env default | Match IDs to request per PUUID |
| `--start` | `0` | Match history offset |
| `--queue` | None | Riot queue ID filter (e.g. `420` for Ranked Solo) |
| `--skip-ranked` | `false` | Skip ranked endpoint |
| `--skip-timelines` | `false` | Skip timeline collection |

---

### crawl-leaderboard

Seed from Riot's ranked leaderboard and crawl recent matches for discovered players.

```bash
# Crawl top 50 Challenger players, 20 matches each
python main.py crawl-leaderboard \
  --platform-region vn2 \
  --tier CHALLENGER \
  --limit 50 \
  --match-count 20

# Crawl all top tiers (Challenger + Grandmaster + Master + Diamond I)
python main.py crawl-leaderboard \
  --platform-region vn2 \
  --tier ALL \
  --limit 50 \
  --match-count 20
```

| Flag | Default | Description |
|---|---|---|
| `--platform-region` | env default | Platform shard |
| `--routing-region` | auto | Match-V5 route |
| `--queue` | `RANKED_SOLO_5x5` | Ranked queue type |
| `--tier` | `ALL` | `ALL`, `CHALLENGER`, `GRANDMASTER`, `MASTER`, `DIAMOND`, etc. |
| `--division` | `I` | Division for non-apex tiers |
| `--limit` | `25` | Max leaderboard players to resolve |
| `--match-count` | env default | Matches to crawl per player |
| `--skip-timelines` | `false` | Skip timeline collection |

---

### crawl-overnight

Safe batched overnight crawl, restricted to VN2. Respects a hard time deadline and stops gracefully if the target match count is reached early.

```bash
# Run for 8 hours, targeting 4000 new matches
python main.py crawl-overnight --hours 8 --target-matches 4000

# Using the PowerShell wrapper (Windows)
.\overnight_crawl.ps1

# Seed from specific tiers only
python main.py crawl-overnight \
  --hours 10 \
  --target-matches 5000 \
  --tiers CHALLENGER GRANDMASTER MASTER DIAMOND:I \
  --leaderboard-limit 50

# Seed from known PUUIDs instead of leaderboard discovery
python main.py crawl-overnight \
  --hours 8 \
  --target-matches 4000 \
  --puuids <PUUID_1> <PUUID_2>
```

| Flag | Default | Description |
|---|---|---|
| `--hours` | env default | Max runtime in hours |
| `--target-matches` | env default | Target new matches to save |
| `--platform-region` | `vn2` | Locked to VN2 |
| `--leaderboard-limit` | env default | Seed players per tier |
| `--match-count` | env default | Match IDs requested per player per page |
| `--batch-size` | env default | New matches per batch before sleeping |
| `--batch-sleep-min` | env default | Min inter-batch sleep (seconds) |
| `--batch-sleep-max` | env default | Max inter-batch sleep (seconds) |
| `--tiers` | env default | Tier plan, e.g. `CHALLENGER GRANDMASTER DIAMOND:I` |
| `--puuids` | None | Optional seed PUUIDs (skips leaderboard discovery) |
| `--queue` | `RANKED_SOLO_5x5` | Queue for leaderboard seeding |
| `--match-queue` | None | Riot queue ID filter applied to match fetching |
| `--skip-timelines` | `false` | Skip timeline collection |

> **Warning**: Passing `--target-matches` beyond the safe capacity for your runtime will log a warning. The crawler will stop at the time limit rather than exceed the configured rate.

---

### process

Rebuild all processed CSVs from existing raw JSON files. No API calls are made.

```bash
python main.py process
```

This is automatically called after each overnight batch and at the end of any crawl command.

---

## Architecture & Data Flow

```
Riot Ranked API
      │
      ▼
 Leaderboard entries ──► PUUIDs (deduplicated seed list)
                               │
                               ▼
                     Match IDs per PUUID
                               │
                    ┌──────────┴──────────┐
                    │                     │
              Checkpoint              Raw file
               check                   check
                    └──────────┬──────────┘
                               │ (skip if already saved)
                               ▼
                     Riot Match Detail API
                               │
                               ▼
                  output/raw/matches/<id>.json
                               │
                               ▼
                     Checkpoint updated
                               │
                               ▼
                     Riot Timeline API
                               │
                               ▼
                 output/raw/timelines/<id>.json
                               │
                               ▼
                     Checkpoint updated
                               │
                               ▼
                    process_raw_to_csv()
                               │
                    ┌──────────┴──────────┐
                    │                     │
             players.csv           matches.csv
             ranked.csv           timelines.csv
```

---

## Capacity Planning

Each full match requires **at minimum 2 API requests** (Match Detail + Timeline). The safe overnight defaults use 20 requests/minute.

| Runtime | Requests | Estimated full matches (with timelines) |
|---|---|---|
| 8 hours | ~9,600 | ~4,300 |
| 10 hours | ~12,000 | ~5,400 |
| 12 hours | ~14,400 | ~6,500 |

> The 90% overhead factor accounts for leaderboard seeding, summoner lookups, and retry delays.

To reach 15,000–25,000 full matches per day:
- Run longer windows (12–16 h)
- Rotate keys responsibly between runs
- Use an approved production key with documented higher limits

The crawler **will not exceed the configured rate** to hit a target. It logs a warning and stops at the deadline instead.

---

## Output Schema

### `output/processed/players.csv`

Per-participant stats for each match.

| Column | Description |
|---|---|
| `match_id` | Riot match ID |
| `participant_id` | In-game participant slot (1–10) |
| `puuid` | Player PUUID |
| `summoner_name` | Summoner name |
| `riot_id_game_name` | Riot ID game name |
| `riot_id_tagline` | Riot ID tagline |
| `champion_id` | Champion numeric ID |
| `champion_name` | Champion name |
| `kills` / `deaths` / `assists` | KDA components |
| `kda` | Computed KDA ratio |
| `damage_dealt` | Total damage dealt to champions |
| `gold_earned` | Total gold earned |
| `vision_score` | Vision score |
| `win` | Boolean win/loss |
| `team_id` | Team (100 or 200) |
| `position` | Assigned position |
| `team_position` | Team-relative position |
| `individual_position` | Individually computed position |

---

### `output/processed/matches.csv`

Match-level metadata.

| Column | Description |
|---|---|
| `match_id` | Riot match ID |
| `game_creation` | Unix timestamp of game creation |
| `game_start_timestamp` | Unix timestamp of game start |
| `game_end_timestamp` | Unix timestamp of game end |
| `game_duration` | Duration in seconds |
| `queue_id` | Riot queue ID |
| `game_version` | Patch version string |
| `map_id` | Map ID |
| `platform_id` | Platform shard (e.g. `VN2`) |
| `game_mode` | Game mode string |
| `game_type` | Game type string |
| `winning_team` | Winning team ID (100 or 200) |
| `participant_count` | Number of participants |

---

### `output/processed/timelines.csv`

One row per timeline event per match.

| Column | Description |
|---|---|
| `match_id` | Riot match ID |
| `timestamp` | Event timestamp (ms) |
| `participant_id` | Participant involved |
| `event_type` | Raw Riot event type string |
| `event_category` | Normalized category |
| `events` | Full event JSON blob |
| `related_participant_id` | Secondary participant |
| `position_x` / `position_y` | Map coordinates |
| `item_id` | Item involved (if applicable) |
| `skill_slot` | Skill leveled (if applicable) |
| `monster_type` / `monster_sub_type` | Monster killed |
| `building_type` / `lane_type` | Building destroyed |
| `ward_type` | Ward placed/killed |
| `killer_id` / `victim_id` / `creator_id` | Event actors |
| `assisting_participant_ids` | Assist participant list |

---

### `output/processed/ranked.csv`

Ranked ladder entry per player per queue.

| Column | Description |
|---|---|
| `puuid` | Player PUUID |
| `summoner_id` | Encrypted summoner ID |
| `queue_type` | Queue string (e.g. `RANKED_SOLO_5x5`) |
| `tier` | Tier (e.g. `CHALLENGER`) |
| `rank` | Division (e.g. `I`) |
| `league_points` | Current LP |
| `wins` / `losses` | Season win/loss counts |
| `win_rate` | Computed win rate |
| `veteran` / `inactive` / `fresh_blood` / `hot_streak` | Ladder flags |

---

## Incremental Guarantees

The crawler avoids duplicate API calls through two independent checks before every request:

**Match Detail:**
1. File exists at `output/raw/matches/<match_id>.json`
2. `match_id` present in `checkpoints/matches.json`

**Timeline:**
1. File exists at `output/raw/timelines/<match_id>.json`
2. `match_id` present in `checkpoints/timelines.json`

Both checks must fail for a request to be issued. After each successful raw file save, the checkpoint is updated **immediately** (not deferred to the end of a batch). This means an interrupted run can be safely resumed from exactly where it stopped — no data is lost and no API quota is wasted.

On startup, `CheckpointManager.hydrate_from_raw_files()` scans existing raw files and back-fills the checkpoint sets, so the system stays consistent even if checkpoints are deleted.

---

## Testing

```bash
pytest -q
```

Test configuration lives in `pytest.ini`. Tests cover the rate limiter, checkpoint manager, schema validation, and CSV output logic.

---

## Contributing

1. Fork the repo and create a feature branch
2. Follow the existing module structure under `crawler/`
3. Add or update tests for any changed behaviour
4. Run `pytest -q` and confirm all tests pass
5. Open a pull request with a clear description of the change

Please do not commit real API keys. The `.gitignore` excludes `.env`, `output/`, `logs/`, and `checkpoints/` by default.