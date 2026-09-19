<#
Live demo over the internet from this laptop (Cloudflare quick tunnels: free, no account).

Before running:
  1. Phone hotspot ON (venue Wi-Fi blocks the MySQL connection).
  2. Parking simulator open with the level loaded (it stays local: the backend talks to it on 127.0.0.1).
  3. Nothing else running on ports 8000 / 5173 (close your own uvicorn / npm run dev).

Run from the repo root:
  powershell -ExecutionPolicy Bypass -File scripts\live-demo.ps1

It prints a public https://....trycloudflare.com link for the judges. Press Enter in this window to stop
everything. The link changes every time the script starts.
#>
param(
    [string]$Cloudflared = "$env:USERPROFILE\tools\cloudflared.exe"
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$shell = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $shell) { $shell = (Get-Command powershell).Source }

if (-not (Test-Path $Cloudflared)) {
    Write-Host "Downloading cloudflared to $Cloudflared ..."
    New-Item -ItemType Directory -Force (Split-Path $Cloudflared) | Out-Null
    Invoke-WebRequest 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' `
        -OutFile $Cloudflared -UseBasicParsing
}
foreach ($port in 8000, 5173) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use: stop your own backend / frontend first, then run this again."
    }
}

function Start-Tunnel([string]$target, [string]$name) {
    $log = Join-Path $env:TEMP "cloudflared-$name.log"
    Remove-Item $log -ErrorAction SilentlyContinue
    $proc = Start-Process $Cloudflared -PassThru -WindowStyle Hidden `
        -ArgumentList 'tunnel', '--url', $target, '--protocol', 'http2', '--logfile', $log
    foreach ($i in 1..45) {
        Start-Sleep 2
        $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($m) { return @{ Url = $m.Matches[0].Value; Process = $proc } }
    }
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    throw "The $name tunnel didn't start (see $log). Check the internet connection."
}

function Start-Window([string]$command) {
    # -EncodedCommand: the command reaches the new window unchanged (no quoting of \ | ( ) etc.)
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    return Start-Process $shell -PassThru -ArgumentList '-NoExit', '-EncodedCommand', $encoded
}

function Wait-Http([string]$url, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try { Invoke-WebRequest $url -TimeoutSec 5 -UseBasicParsing | Out-Null; return $true } catch { Start-Sleep 3 }
    }
    return $false
}

Write-Host "Starting tunnels..."
$backend = Start-Tunnel 'http://127.0.0.1:8000' 'backend'
$frontend = Start-Tunnel 'http://localhost:5173' 'frontend'

# The frontend in the judges' browser must call the backend through its tunnel (git-ignored file, removed at the end)
$envLocal = Join-Path $root 'web-interface\.env.local'
Set-Content $envLocal "VITE_API_BASE_URL=$($backend.Url)" -Encoding utf8

# Backend: allow the tunnel origins (CORS); no --reload so a file save can't restart it mid-demo
$backendCmd = @"
`$env:CORS_ORIGIN_REGEX = 'https?://(localhost|127\.0\.0\.1)(:\d+)?|https://[a-z0-9-]+\.trycloudflare\.com'
Set-Location '$root\backend\fastapi_project'
..\fastapi-env\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000
"@
$backendWin = Start-Window $backendCmd

# Frontend: let Vite answer requests for the tunnel hostname
$frontendCmd = @"
`$env:__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS = '.trycloudflare.com'
Set-Location '$root\web-interface'
npm run dev -- --port 5173 --strictPort
"@
$frontendWin = Start-Window $frontendCmd

Write-Host "Waiting for the backend (MySQL + simulator sync take ~30-40 s) and the frontend..."
$okB = Wait-Http "$($backend.Url)/health" 150
$okF = Wait-Http $frontend.Url 90

Write-Host ""
Write-Host "=============================================================="
Write-Host " DEMO LINK (send this):  $($frontend.Url)" -ForegroundColor Green
Write-Host " backend (API docs):      $($backend.Url)/docs"
Write-Host " backend reachable: $okB   frontend reachable: $okF"
Write-Host " login: your dashboard admin account"
Write-Host "=============================================================="
Write-Host "Keep this laptop awake and online. Press Enter here to stop everything."
[void](Read-Host)

foreach ($p in $backend.Process, $frontend.Process, $backendWin, $frontendWin) {
    if ($p) { & taskkill /PID $p.Id /T /F 2>$null | Out-Null }
}
Remove-Item $envLocal -ErrorAction SilentlyContinue
Write-Host "Stopped. Local development uses http://127.0.0.1:8000 again."
