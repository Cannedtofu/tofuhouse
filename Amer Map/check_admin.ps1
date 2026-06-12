$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]$identity
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "YES — this session is running as Administrator" -ForegroundColor Green
} else {
    Write-Host "NO — this session is NOT elevated. Right-click PowerShell and choose 'Run as Administrator'" -ForegroundColor Red
}
