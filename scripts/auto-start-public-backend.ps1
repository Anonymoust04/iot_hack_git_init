<#
Runs at Windows sign-in. Keep this laptop awake and connected to the phone hotspot
for the Vercel site to reach FastAPI through the reserved ngrok domain.

The simulator window opens for you to load the level. Its Windows UI is local;
visitors use the Vercel dashboard. Sign out to stop the monitor. Removing
the Startup shortcut prevents it from launching at future sign-ins.
#>
param(
    [Parameter(Mandatory = $true)][string]$Domain,
    [string]$Ngrok = "$env:USERPROFILE\tools\ngrok.exe",
    [string]$Simulator = "$env:USERPROFILE\myProject\ParkingSimulator\ParkingSimulator-win-x64\ParkingSimulator.exe"
)

$ErrorActionPreference = 'Stop'
$Domain = $Domain -replace '^https?://', '' -replace '/$', ''
if ($Domain -notmatch '^[a-z0-9.-]+$') { throw 'Domain must be a hostname, without a path or port.' }

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'backend\fastapi-env\Scripts\python.exe'
$backend = Join-Path $root 'backend\fastapi_project'
$envFile = Join-Path $root '.env'
$logDir = Join-Path $env:LOCALAPPDATA 'ParkControl'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$startupLog = Join-Path $logDir 'auto-start.log'

function Write-State([string]$message) {
    Add-Content -LiteralPath $startupLog -Value "$(Get-Date -Format s) $message"
}

function Test-Port([string]$server, [int]$port) {
    $socket = [System.Net.Sockets.TcpClient]::new()
    try {
        $connect = $socket.ConnectAsync($server, $port)
        return $connect.Wait(3000) -and $socket.Connected
    } catch { return $false }
    finally { $socket.Dispose() }
}

function Test-Backend {
    try {
        $response = Invoke-WebRequest 'http://127.0.0.1:8000/health' -TimeoutSec 4 -UseBasicParsing
        return $response.StatusCode -eq 200
    } catch { return $false }
}

function Test-Tunnel([string]$name) {
    try {
        $tunnels = Invoke-RestMethod 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 4
        return [bool]($tunnels.tunnels | Where-Object { $_.public_url -eq "https://$name" })
    } catch { return $false }
}

function Get-DatabaseAddress {
    $settings = @{}
    if (Test-Path -LiteralPath $envFile) {
        foreach ($line in Get-Content -LiteralPath $envFile) {
            if ($line -match '^\s*(DB_HOST|DB_PORT)\s*=\s*(.*?)\s*$') {
                $settings[$matches[1]] = $matches[2].Trim('"', "'")
            }
        }
    }
    $dbHostName = if ($env:DB_HOST) { $env:DB_HOST } else { $settings['DB_HOST'] }
    $dbPort = if ($env:DB_PORT) { $env:DB_PORT } elseif ($settings['DB_PORT']) { $settings['DB_PORT'] } else { '3306' }
    if (-not $dbHostName) { return $null }
    return @{ HostName = $dbHostName; Port = [int]$dbPort }
}

$simulatorAttempted = $false
$backendProcess = $null
$ngrokProcess = $null
$lastState = ''
Write-State "Starting sign-in monitor for https://$Domain"

while ($true) {
    try {
        if (-not $simulatorAttempted) {
            $simulatorAttempted = $true
            if (-not (Get-Process ParkingSimulator -ErrorAction SilentlyContinue)) {
                if (Test-Path -LiteralPath $Simulator) {
                    # The simulator is interactive: the operator still loads its level.
                    Start-Process -FilePath $Simulator -WorkingDirectory (Split-Path -Parent $Simulator) -WindowStyle Normal | Out-Null
                    Write-State 'Opened ParkingSimulator; load the level in its window.'
                } else { Write-State "Simulator executable not found at $Simulator" }
            }
        }

        if (-not (Test-Backend)) {
            $dbAddress = Get-DatabaseAddress
            if (-not (Test-Path -LiteralPath $python)) {
                $state = 'FastAPI virtual environment is missing; create backend/fastapi-env.'
            } elseif (-not $dbAddress) {
                $state = 'DB_HOST is missing from the repo .env file.'
            } elseif (-not (Test-Port $dbAddress.HostName $dbAddress.Port)) {
                $state = 'Waiting for MySQL network connection (turn on the phone hotspot).'
            } elseif (Test-Port '127.0.0.1' 8000) {
                $state = 'Port 8000 is occupied; waiting for its health endpoint.'
            } elseif ($backendProcess -and -not $backendProcess.HasExited) {
                $state = 'Waiting for FastAPI startup.'
            } else {
                $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
                $backendProcess = Start-Process -FilePath $python -PassThru -WindowStyle Hidden -WorkingDirectory $backend `
                    -ArgumentList @('-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000') `
                    -RedirectStandardOutput (Join-Path $logDir "backend-$stamp.log") `
                    -RedirectStandardError (Join-Path $logDir "backend-$stamp.err.log")
                $state = "Started FastAPI (PID $($backendProcess.Id))."
            }
        } else {
            if (Test-Tunnel $Domain) {
                $state = "Public backend ready at https://$Domain"
            } elseif (-not (Test-Path -LiteralPath $Ngrok)) {
                $state = "ngrok executable not found at $Ngrok"
            } elseif ($ngrokProcess -and -not $ngrokProcess.HasExited) {
                $state = 'Waiting for the ngrok tunnel.'
            } elseif (Get-Process ngrok -ErrorAction SilentlyContinue) {
                $state = 'Another ngrok tunnel is running; check its domain at http://127.0.0.1:4040.'
            } else {
                $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
                $ngrokProcess = Start-Process -FilePath $Ngrok -PassThru -WindowStyle Hidden `
                    -ArgumentList @('http', "--url=https://$Domain", '8000') `
                    -RedirectStandardOutput (Join-Path $logDir "ngrok-$stamp.log") `
                    -RedirectStandardError (Join-Path $logDir "ngrok-$stamp.err.log")
                $state = "Started ngrok (PID $($ngrokProcess.Id))."
            }
        }
        if ($state -ne $lastState) { Write-State $state; $lastState = $state }
    } catch {
        Write-State "Startup check failed: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 20
}
