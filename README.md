# Riot-esports-data-ingestion

Safe, incremental Riot Games crawler for daily runs with a rotated development API key. The project saves full raw JSON first, then rebuilds clean analytics-ready CSVs without duplicating already crawled matches.

## What This Project Prioritizes

- Easy API key rotation through `.env`
- Strong checkpointing for daily incremental crawls
- No duplicate match detail requests
- Conservative Riot API usage for development keys
- Clean raw and processed data for analytics and ML

## Project Structure

```text
Riot-esports-data-ingestion/
|-- requirements.txt
|-- .env.example
|-- README.md
|-- main.py
|-- crawler/
|   |-- config/
|   |   `-- settings.py
|   |-- utils/
|   |   |-- checkpoint.py
|   |   |-- rate_limiter.py
|   |   |-- retry.py
|   |   |-- logger.py
|   |   `-- helpers.py
|   |-- api/
|   |   `-- riot_client.py
|   |-- schemas/
|   |   `-- models.py
|   `-- services/
|       |-- summoner_service.py
|       |-- match_service.py
|       |-- ranked_service.py
|       `-- timeline_service.py
|-- output/
|   |-- raw/
|   |   |-- summoners/
|   |   |-- matches/
|   |   |-- timelines/
|   |   `-- ranked/
|   `-- processed/
|       |-- players.csv
|       |-- matches.csv
|       |-- ranked.csv
|       `-- timelines.csv
|-- checkpoints/
`-- tests/
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Set your current Riot key in `.env`:

```text
RIOT_API_KEY=RGAPI-your-current-daily-key
```

To rotate the key each day, only replace `RIOT_API_KEY`. Checkpoints and raw JSON stay unchanged, so old matches are not crawled again.

## Safe Defaults

```text
MAX_CONCURRENCY=6
REQUESTS_PER_MINUTE=40
METHOD_REQUESTS_PER_MINUTE=20
REQUEST_SLEEP_MIN_SECONDS=1.2
REQUEST_SLEEP_MAX_SECONDS=1.8
RETRY_ATTEMPTS=5
RETRY_BASE_DELAY_SECONDS=1.0
CIRCUIT_BREAKER_FAILURE_THRESHOLD=5
CIRCUIT_BREAKER_COOLDOWN_SECONDS=300
USER_AGENT=RiotDataCrawler/1.0 (Personal Educational Project)
```

The code caps risky values and logs a warning if configuration is too aggressive.

## Daily Crawl by PUUID

```bash
python main.py crawl-puuids --puuids <puuid1> <puuid2> --platform-region vn2 --match-count 30 --skip-timelines
```

Without `--skip-timelines`, timelines are crawled sequentially after new matches with an extra delay.

## Crawl from Ranked Leaderboard

```bash
python main.py crawl-leaderboard --platform-region vn2 --tier MASTER --limit 10 --match-count 10
```

Keep leaderboard limits small when using a development key.

## Rebuild Processed CSVs

```bash
python main.py process
```

This rebuilds processed CSVs from raw JSON only. It does not call Riot API.

## Incremental Behavior

Before calling match detail, the crawler checks:

- `output/raw/matches/<match_id>.json`
- `checkpoints/matches.json`

If either exists, the match detail API call is skipped. After every successful raw match save, the match ID is immediately written to the checkpoint.

Timeline calls use the same pattern with:

- `output/raw/timelines/<match_id>.json`
- `checkpoints/timelines.json`

## Outputs

Raw JSON:

- `output/raw/summoners/`
- `output/raw/matches/`
- `output/raw/timelines/`
- `output/raw/ranked/`

Processed CSV:

- `output/processed/players.csv`
- `output/processed/matches.csv`
- `output/processed/ranked.csv`
- `output/processed/timelines.csv`

## Tests

```bash
pytest -q
```
