param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 5000,
    [string]$SeedRange = "medium",
    [switch]$SkipSeed
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Virtual environment Python was not found at: $python" -ForegroundColor Red
    Write-Host "Create/install the venv first, then run this script again." -ForegroundColor Yellow
    exit 1
}

$env:FLASK_ENV = "development"
$env:FLASK_DEBUG = "0"

Write-Host "Bootstrapping CivikIndia database..." -ForegroundColor Cyan
& $python deploy\bootstrap.py

if (-not $SkipSeed) {
    Write-Host "Seeding demo data ($SeedRange)..." -ForegroundColor Cyan
    & $python seed.py --target-range $SeedRange --clear-existing-complaints --seed-audit-logs
}

Write-Host ""
Write-Host "Starting CivikIndia on http://$HostAddress`:$Port" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop the server." -ForegroundColor DarkGray
& $python -m flask --app wsgi:app run --host $HostAddress --port $Port --no-reload
