# Run this once as Administrator to register the quarterly scrape tasks.
# Right-click PowerShell -> "Run as Administrator", then:
#   cd "D:\代码项目\Amer Map"
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\setup_schedule.ps1

$bat = "D:\代码项目\Amer Map\run_quarterly.bat"

$quarters = @(
    @{ Name = "AmerMap-Q1-End"; Month = "March";     MonthNum = "03"; Day = "31" },
    @{ Name = "AmerMap-Q2-End"; Month = "June";      MonthNum = "06"; Day = "30" },
    @{ Name = "AmerMap-Q3-End"; Month = "September"; MonthNum = "09"; Day = "30" },
    @{ Name = "AmerMap-Q4-End"; Month = "December";  MonthNum = "12"; Day = "31" }
)

foreach ($q in $quarters) {
    Unregister-ScheduledTask -TaskName $q.Name -Confirm:$false -ErrorAction SilentlyContinue

    $xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Amer Map quarterly store-locator scrape — $($q.Month) $($q.Day)</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-$($q.MonthNum)-$($q.Day)T09:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByMonth>
        <DaysOfMonth><Day>$($q.Day)</Day></DaysOfMonth>
        <Months><$($q.Month) /></Months>
      </ScheduleByMonth>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT4H</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions>
    <Exec>
      <Command>$bat</Command>
    </Exec>
  </Actions>
</Task>
"@

    try {
        Register-ScheduledTask -TaskName $q.Name -Xml $xml -Force -ErrorAction Stop | Out-Null
        Write-Host "  [OK]   $($q.Name)  — $($q.Month) $($q.Day) at 09:00" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] $($q.Name): $_" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Done. Verify with:"
Write-Host "  Get-ScheduledTask -TaskName 'AmerMap*' | Select TaskName, State"
