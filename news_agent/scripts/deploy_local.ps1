# scripts/deploy_local.ps1
# Usage: .\scripts\deploy_local.ps1 "your commit message"
# If no message is given, you will be prompted.

param(
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"

$ServerUser = "root"
$ServerHost = "47.239.66.248"
$ServerPort = "2222"
$SshKey     = "$HOME\.ssh\id_ed25519"
$RemoteCmd  = "bash /opt/tofuhouse/news_agent/scripts/deploy.sh"

# --- Commit message ----------------------------------------------------------
if (-not $Message) {
    $Message = Read-Host "Commit message"
}
if (-not $Message) {
    Write-Error "Commit message is required."
    exit 1
}

# --- Git: stage news_agent folder, commit, push ------------------------------
$repoRoot = Split-Path -Parent $PSScriptRoot   # d:\代码项目\news_agent
# Derive git root from known folder structure to avoid encoding issues with
# git rev-parse --show-toplevel on Chinese paths in PowerShell 5.1 (GBK code page).
$gitRoot  = Split-Path -Parent $repoRoot       # d:\代码项目
$relPath  = Split-Path -Leaf $repoRoot         # news_agent

Write-Host ""
Write-Host "==> Staging changes in news_agent..." -ForegroundColor Cyan
git -C $gitRoot add $relPath

$status = git -C $gitRoot status --short -- $relPath
if (-not $status) {
    Write-Host "Nothing to commit. Skipping to deploy." -ForegroundColor Yellow
} else {
    Write-Host $status
    Write-Host ""
    Write-Host "==> Committing..." -ForegroundColor Cyan
    git -C $gitRoot commit -m $Message
    Write-Host ""
    Write-Host "==> Pushing..." -ForegroundColor Cyan
    git -C $gitRoot push
}

# --- SSH deploy --------------------------------------------------------------
Write-Host ""
Write-Host "==> Deploying on server..." -ForegroundColor Cyan
ssh -p $ServerPort -i $SshKey "${ServerUser}@${ServerHost}" $RemoteCmd

Write-Host ""
Write-Host "Done." -ForegroundColor Green
