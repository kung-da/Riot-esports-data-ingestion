# Riot-esports-data-ingestion

Safe incremental Riot Games crawler for VN2. It saves full raw Match Detail and Timeline JSON first, then rebuilds analytics-ready CSVs.

## Important Capacity Note

With full data (`match detail + timeline`) each match costs at least 2 Riot API requests. At the safe overnight setting of about 20 requests/minute:

- 8 hours ~= 9,600 requests ~= 4,300 safe full matches after overhead
- 10 hours ~= 12,000 requests ~= 5,400 safe full matches after overhead

The CLI accepts `--target-matches 20000`, but the crawler will log a warning and stop by the time limit rather than forcing unsafe speed. To reach 15,000-25,000 full matches/day with a development key, run longer windows, rotate keys responsibly, or use an approved production key with documented higher limits.

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

To rotate the key daily, replace only `RIOT_API_KEY`. Raw JSON and checkpoints stay unchanged.

## Safe Defaults

```text
MAX_CONCURRENCY=5
REQUESTS_PER_MINUTE=20
METHOD_REQUESTS_PER_MINUTE=12
REQUEST_SLEEP_MIN_SECONDS=1.8
REQUEST_SLEEP_MAX_SECONDS=3.5
OVERNIGHT_BATCH_SIZE=1000
OVERNIGHT_BATCH_SLEEP_MIN_SECONDS=480
OVERNIGHT_BATCH_SLEEP_MAX_SECONDS=900
OVERNIGHT_INCLUDE_TIMELINES=true
```

The limiter uses global, per-region, and per-method windows, exponential backoff with jitter, circuit breaker, and preemptive slowdown from Riot rate-limit headers.

## Commands

Crawl a single leaderboard tier:

```bash
python main.py crawl-leaderboard --platform-region vn2 --tier CHALLENGER --limit 50 --match-count 20
```

Crawl all top tiers configured in `.env`:

```bash
python main.py crawl-leaderboard --platform-region vn2 --tier ALL --limit 50 --match-count 20
```

Run overnight safe mode:

```bash
python main.py crawl-overnight --hours 8 --target-matches 20000
```

Rebuild processed CSVs:

```bash
python main.py process
```

PowerShell wrapper:

```powershell
.\overnight_crawl.ps1
```

## Incremental Guarantees

Before calling Match Detail, the crawler checks:

- `output/raw/matches/<match_id>.json`
- `checkpoints/matches.json`

Before calling Timeline, it checks:

- `output/raw/timelines/<match_id>.json`
- `checkpoints/timelines.json`

After every successful raw save, the checkpoint is updated immediately. Re-running the same crawl does not duplicate match detail or timeline API calls.

## Output

Raw JSON:

- `output/raw/summoners/`
- `output/raw/ranked/`
- `output/raw/matches/`
- `output/raw/timelines/`

Processed CSV:

- `output/processed/players.csv`
- `output/processed/matches.csv`
- `output/processed/timelines.csv`
- `output/processed/ranked.csv`

Logs:

- `logs/<command>_YYYYMMDD_HHMMSS.log`

## Tests

```bash
pytest -q
```
