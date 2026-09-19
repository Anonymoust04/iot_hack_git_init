<#
Publish the backend (running on this laptop, next to the simulator) at a FIXED public https address,
so the frontend deployed on Vercel can reach it. Uses ngrok's free static domain.

One-time setup (5 min):
  1. Sign up at https://dashboard.ngrok.com (free).
  2. Copy your authtoken (Getting Started -> Your Authtoken) and run:  ngrok config add-authtoken <token>
     (this script installs ngrok first if it's missing; run it once, then add the token)
  3. Domains -> "Create Domain" (free static domain), e.g. park-control.ngrok-free.app

Every demo (phone hotspot ON, simulator open, nothing else on port 8000):
  powershell -ExecutionPolicy Bypass -File scripts\public-backend.ps1 -Domain park-control.ngrok-free.app

Vercel's VITE_API_BASE_URL must be https://<that domain>. Press Enter here to stop.
#>
param(
    [string]$Domain = $env:NGROK_DOMAIN,
    [string]$Ngrok = "$env:USERPROFILE\tools\ngrok.exe"
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$shell = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $shell) { $shell = (Get-Command powershell).Source }

if (-not (Test-Path $Ngrok)) {
    $found = (Get-Command ngrok -ErrorAction SilentlyContinue).Source
    if ($found) { $Ngrok = $found } else {
        Write-Host "Installing ngrok to $Ngrok ..."
        New-Item -ItemType Directory -Force (Split-Path $Ngrok) | Out-Null
        $zip = Join-Path $env:TEMP 'ngrok.zip'
        Invoke-WebRequest 'https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip' -OutFile $zip -UseBasicParsing
        Expand-Archive $zip -DestinationPath (Split-Path $Ngrok) -Force
    }
}
if (-not $Domain) {
    throw "Give your ngrok static domain: -Domain your-name.ngrok-free.app (or set NGROK_DOMAIN). See the setup steps at the top of this file."
}
$Domain = $Domain -replace '^https?://', '' -replace '/$', ''
& $Ngrok config check *> $null
if ($LASTEXITCODE -ne 0) { throw "ngrok has no authtoken yet: run  `"$Ngrok`" config add-authtoken <your token>  once, then run this again." }
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    throw "Port 8000 is already in use: stop your own backend first (only ONE backend may drive the simulator)."
}

function Start-Window([string]$command) {
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    return Start-Process $shell -PassThru -ArgumentList '-NoExit', '-EncodedCommand', $encoded
}

# Backend: allow the Vercel site (and local dev) to call it; no --reload during a demo
$backendWin = Start-Window @"
`$env:CORS_ORIGIN_REGEX = 'https?://(localhost|127\.0\.0\.1)(:\d+)?|https://[a-z0-9-]+\.vercel\.app'
Set-Location '$root\backend\fastapi_project'
..\fastapi-env\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000
"@
$tunnel = Start-Process $Ngrok -PassThru -WindowStyle Minimized -ArgumentList 'http', "--url=https://$Domain", '8000'

Write-Host "Waiting for the backend (MySQL + simulator sync take ~30-40 s) and the tunnel..."
$public = "https://$Domain"
$ok = $false
foreach ($i in 1..50) {
    Start-Sleep 3
    try {
        Invoke-WebRequest "$public/health" -Headers @{ 'ngrok-skip-browser-warning' = '1' } -TimeoutSec 5 -UseBasicParsing | Out-Null
        $ok = $true; break
    } catch {}
}
Write-Host ""
Write-Host "=============================================================="
Write-Host " Public backend: $public   (reachable: $ok)" -ForegroundColor Green
Write-Host " API docs:       $public/docs"
Write-Host " Vercel env:     VITE_API_BASE_URL=$public"
Write-Host "=============================================================="
Write-Host "Keep the laptop awake and online. Press Enter here to stop."
[void](Read-Host)
foreach ($p in $tunnel, $backendWin) { if ($p) { & taskkill /PID $p.Id /T /F 2>$null | Out-Null } }
Write-Host "Stopped."
