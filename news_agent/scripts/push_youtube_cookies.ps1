# scripts/push_youtube_cookies.ps1
#
# Push a local YouTube cookies file to the server.
#
# SETUP (one-time):
#   1. Install the "Get cookies.txt LOCALLY" extension in Edge/Chrome
#      (Edge: https://microsoftedge.microsoft.com/addons/ — search "Get cookies.txt")
#   2. Go to youtube.com while logged in, click the extension icon, export cookies.
#      Save the file to the path set in $LocalCookieFile below.
#   3. Run this script (or let Task Scheduler run it).
#
# AUTOMATION:
#   Cookies typically last 3-6 months. When transcript jobs start failing again,
#   re-export from the browser extension and re-run this script.
#
#   To register as a weekly Task Scheduler job (run once in elevated PowerShell):
#
#   $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
#                -Argument "-NonInteractive -File `"d:\代码项目\news_agent\scripts\push_youtube_cookies.ps1`""
#   $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "09:00AM"
#   $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
#   Register-ScheduledTask -TaskName "PushYouTubeCookies" `
#       -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest

param(
    [string]$LocalCookieFile = "D:\youtube_cookies.txt"
)

$ErrorActionPreference = "Stop"

$serverUser = "root"
$serverHost = "47.239.66.248"
$serverPort = "2222"
$serverPath = "/opt/tofuhouse/news_agent/youtube_cookies.txt"
$sshKey     = "$HOME\.ssh\id_ed25519"

if (-not (Test-Path $LocalCookieFile)) {
    Write-Error @"
Cookie file not found at: $LocalCookieFile

To export cookies:
  1. Install "Get cookies.txt LOCALLY" in Edge/Chrome
  2. Log in to youtube.com
  3. Click the extension icon → Export → save to $LocalCookieFile
  4. Re-run this script
"@
    exit 1
}

$ageDays = (New-TimeSpan -Start (Get-Item $LocalCookieFile).LastWriteTime -End (Get-Date)).Days
if ($ageDays -gt 90) {
    Write-Warning "Cookie file is $ageDays days old — consider re-exporting from the browser."
}

Write-Host "Uploading cookies ($ageDays days old) to server..."
scp -P $serverPort -i $sshKey $LocalCookieFile "${serverUser}@${serverHost}:${serverPath}"

Write-Host "Done. YouTube cookies refreshed on server."
