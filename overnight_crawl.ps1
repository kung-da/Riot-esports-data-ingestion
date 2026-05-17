# Safe overnight VN2 crawl.
# Rotate RIOT_API_KEY in .env before starting a new nightly run.

cd D:\Project\Riot-esports-data-ingestion

New-Item -ItemType Directory -Force -Path logs | Out-Null
$logFile = "logs\overnight_shell_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

"===== Starting overnight crawl at $(Get-Date) =====" | Tee-Object -FilePath $logFile -Append

python main.py crawl-overnight `
    --hours 8 `
    --target-matches 20000 `
    --platform-region vn2 `
    --leaderboard-limit 50 `
    --match-count 20 `
    --batch-size 1000 `
    --batch-sleep-min 480 `
    --batch-sleep-max 900 2>&1 | Tee-Object -FilePath $logFile -Append

python main.py process 2>&1 | Tee-Object -FilePath $logFile -Append

"===== Overnight crawl finished at $(Get-Date) =====" | Tee-Object -FilePath $logFile -Append
