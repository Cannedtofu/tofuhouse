#!/usr/bin/env pwsh
# pull_logs.ps1 — fetch app.log from the cloud server to logs/app.log locally.
# Usage: .\scripts\pull_logs.ps1

$SERVER = "root@47.239.66.248"
$REMOTE  = "/opt/tofuhouse/news_agent/logs/app.log"
$LOCAL   = "$PSScriptRoot\..\logs\app.log"

Write-Host "==> Pulling app.log from $SERVER..." -ForegroundColor Cyan
scp -P 2222 "${SERVER}:${REMOTE}" $LOCAL

if ($LASTEXITCODE -eq 0) {
    $lines = (Get-Content $LOCAL | Measure-Object -Line).Lines
    Write-Host "==> Done. $lines lines saved to logs/app.log" -ForegroundColor Green
} else {
    Write-Host "==> scp failed (exit $LASTEXITCODE)" -ForegroundColor Red
}
