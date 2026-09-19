<#
Create or remove a per-user Windows Startup shortcut for the public backend.
Run once from the repo root, while your ngrok tunnel is up (or pass -Domain):

  powershell -ExecutionPolicy Bypass -File scripts\install-public-autostart.ps1
  powershell -ExecutionPolicy Bypass -File scripts\install-public-autostart.ps1 -Remove

The shortcut starts at your next sign-in. The laptop must remain on and online.
#>
param(
    [string]$Domain = $env:NGROK_DOMAIN,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$startup = [Environment]::GetFolderPath('Startup')
if (-not $startup) { throw 'Windows Startup folder was not found.' }
$shortcutPath = Join-Path $startup 'ParkControl Public Backend.lnk'

if ($Remove) {
    if (Test-Path -LiteralPath $shortcutPath) { Remove-Item -LiteralPath $shortcutPath }
    Write-Output "Removed $shortcutPath"
    return
}

if (-not $Domain) {
    try {
        $tunnels = Invoke-RestMethod 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 4
        $current = $tunnels.tunnels | Where-Object { $_.config.addr -match ':8000$' } | Select-Object -First 1
        if ($current) { $Domain = ([uri]$current.public_url).Host }
    } catch { }
}
if (-not $Domain) { throw 'Pass -Domain your-reserved-ngrok-domain or start the ngrok tunnel first.' }
$Domain = $Domain -replace '^https?://', '' -replace '/$', ''
if ($Domain -notmatch '^[a-z0-9.-]+$') { throw 'Domain must be a hostname, without a path or port.' }

$launcher = Join-Path $PSScriptRoot 'auto-start-public-backend.ps1'
if (-not (Test-Path -LiteralPath $launcher)) { throw "Launcher not found: $launcher" }
$powershell = Join-Path $PSHOME 'powershell.exe'
if (-not (Test-Path -LiteralPath $powershell)) { $powershell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe' }

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $powershell
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`" -Domain `"$Domain`""
$shortcut.WorkingDirectory = Split-Path -Parent $PSScriptRoot
$shortcut.Description = 'Start ParkControl simulator, FastAPI and ngrok at sign-in'
$shortcut.Save()
Write-Output "Installed $shortcutPath for https://$Domain. It starts at your next sign-in."
