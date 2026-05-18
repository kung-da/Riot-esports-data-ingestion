# overnight_safe.ps1
# Safe overnight crawl cho i5-12450HX
# Tắt sleep khi cắm điện
powercfg /change standby-timeout-ac 0

cd D:\Project\Riot-esports-data-ingestion

New-Item -ItemType Directory -Force -Path logs | Out-Null
$logFile = "logs\overnight_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

"===== START OVERNIGHT CRAWL - $(Get-Date) =====" | Tee-Object -FilePath $logFile -Append
"Target: ~15k-20k matches | VN2 | Full Timeline" | Tee-Object -FilePath $logFile -Append

try {
    python main.py crawl-overnight `
        --hours 8 `
        --target-matches 20000 `
        --platform-region vn2 `
        --skip-timelines 2>&1 | Tee-Object -FilePath $logFile -Append

    "===== Processing data to CSV =====" | Tee-Object -FilePath $logFile -Append
    python main.py process 2>&1 | Tee-Object -FilePath $logFile -Append

    "===== OVERNIGHT CRAWL FINISHED SUCCESSFULLY at $(Get-Date) =====" | Tee-Object -FilePath $logFile -Append
}
catch {
    "===== ERROR OCCURRED: $($_.Exception.Message) =====" | Tee-Object -FilePath $logFile -Append
}
finally {
    "Script ended at $(Get-Date)" | Tee-Object -FilePath $logFile -Append
    
    powercfg /change standby-timeout-ac 15
}