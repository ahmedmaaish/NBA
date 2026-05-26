# NBA Assistant — Hourly local updater
# Runs the scanner, commits new signals.json, pushes to GitHub
# Pages auto-deploys from /docs on push.

$ErrorActionPreference = "Continue"
$repo = "C:\Users\Ahmed Maaish\Desktop\Python\nba-assistant"
Set-Location $repo

$logFile = "$repo\update.log"
$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ"
"=== $ts ===" | Out-File -FilePath $logFile -Append -Encoding utf8

try {
    # Run the scanner — uses system Python (must have requirements installed)
    $output = python -m scanner.update 2>&1
    $output | Out-File -FilePath $logFile -Append -Encoding utf8

    # Check if signals.json changed
    $status = git status --porcelain docs/data/signals.json
    if ([string]::IsNullOrWhiteSpace($status)) {
        "No changes to signals.json — nothing to push." | Out-File -FilePath $logFile -Append -Encoding utf8
        exit 0
    }

    # Commit and push
    git add docs/data/signals.json
    git commit -m "chore: update signals $ts" 2>&1 | Out-File -FilePath $logFile -Append -Encoding utf8
    git push origin main 2>&1 | Out-File -FilePath $logFile -Append -Encoding utf8
    "Pushed update at $ts" | Out-File -FilePath $logFile -Append -Encoding utf8
} catch {
    "ERROR: $($_.Exception.Message)" | Out-File -FilePath $logFile -Append -Encoding utf8
    exit 1
}
