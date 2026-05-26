# scripts/push_youtube_cookies.ps1
# Refresh YouTube cookies on the server from your local browser.
# Windows Task Scheduler runs this weekly (set up once with the Register-ScheduledTask
# command in the README or run manually when you need to refresh cookies).

$ErrorActionPreference = "Stop"

$tempCookies = "$env:TEMP\yt_cookies_push.txt"
$serverUser  = "root"
$serverHost  = "47.239.66.248"
$serverPort  = "2222"
$serverPath  = "/opt/tofuhouse/news_agent/youtube_cookies.txt"
$sshKey      = "$HOME\.ssh\id_ed25519"
$ytdlp       = "d:\代码项目\news_agent\.venv\Scripts\yt-dlp.exe"

Write-Host "Extracting YouTube cookies from Edge browser..."
# yt-dlp reads from Edge (change 'edge' to 'chrome' if using Chrome),
# writes Netscape-format cookies to $tempCookies
& $ytdlp --cookies-from-browser edge --cookies $tempCookies --skip-download `
    --quiet "https://www.youtube.com/" 2>&1 | Out-Null

if (-not (Test-Path $tempCookies)) {
    Write-Error "Cookie export failed — yt-dlp produced no file."
    exit 1
}

Write-Host "Uploading cookies to server..."
scp -P $serverPort -i $sshKey $tempCookies "${serverUser}@${serverHost}:${serverPath}"

Remove-Item $tempCookies -ErrorAction SilentlyContinue
Write-Host "Done. YouTube cookies refreshed on server."

# To register as a weekly Task Scheduler job (run once in elevated PowerShell):
#
# $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
#              -Argument "-NonInteractive -File `"d:\代码项目\news_agent\scripts\push_youtube_cookies.ps1`""
# $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "09:00AM"
# $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
# Register-ScheduledTask -TaskName "RefreshYouTubeCookies" `
#     -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest
