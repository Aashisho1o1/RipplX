[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [switch]$SkipBackup
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$webDist = Join-Path $root "web\dist"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing .venv. Install the Python dependencies before starting RipplX."
}
if (-not (Test-Path -LiteralPath (Join-Path $webDist "index.html"))) {
    throw "Missing web/dist. Build the frontend before starting RipplX."
}

$dbPath = if ($env:FINWATCH_DB) { $env:FINWATCH_DB } else { "data\finwatch.db" }
if (-not [IO.Path]::IsPathRooted($dbPath)) {
    $dbPath = Join-Path $root $dbPath
}
$dbPath = [IO.Path]::GetFullPath($dbPath)
$dbDirectory = Split-Path -Parent $dbPath
New-Item -ItemType Directory -Path $dbDirectory -Force | Out-Null

if ((Test-Path -LiteralPath $dbPath) -and -not $SkipBackup) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backup = Join-Path $dbDirectory "finwatch-demo-backup-$stamp.db"
    Copy-Item -LiteralPath $dbPath -Destination $backup
    Write-Host "Database backup: $backup"
}

$env:FINWATCH_DB = $dbPath
$env:FINWATCH_WEB_DIST = $webDist

Write-Host "RipplX: http://127.0.0.1:$Port"
Write-Host "Health check: http://127.0.0.1:$Port/api/bootstrap"
& $python -m uvicorn finwatch.web.app:create_app --factory --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
