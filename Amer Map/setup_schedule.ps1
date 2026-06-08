# Run this once as Administrator to register the quarterly scrape tasks.
# Right-click PowerShell → "Run as Administrator", then:
#   cd "D:\代码项目\Amer Map"
#   .\setup_schedule.ps1

$bat = "D:\代码项目\Amer Map\run_quarterly.bat"

$tasks = @(
    @{ Name = "AmerMap-Q1-End"; Month = "MAR"; Day = 31 },
    @{ Name = "AmerMap-Q2-End"; Month = "JUN"; Day = 30 },
    @{ Name = "AmerMap-Q3-End"; Month = "SEP"; Day = 30 },
    @{ Name = "AmerMap-Q4-End"; Month = "DEC"; Day = 31 }
)

foreach ($t in $tasks) {
    # Remove if already registered (makes script idempotent)
    schtasks /Delete /TN $t.Name /F 2>$null | Out-Null

    $result = schtasks /Create `
        /TN  $t.Name `
        /TR  "`"$bat`"" `
        /SC  MONTHLY `
        /M   $t.Month `
        /D   $t.Day `
        /ST  "09:00" `
        /RL  HIGHEST `
        /F 2>&1

    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK]   $($t.Name)  — runs every $($t.Month) $($t.Day) at 09:00" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $($t.Name): $result" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Registered tasks (verify with Task Scheduler or run: schtasks /Query /TN AmerMap* /FO LIST)"
