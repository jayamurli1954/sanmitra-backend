param(
    [int]$BackendPort = 8000,
    [switch]$SkipServiceStart
)

$ErrorActionPreference = 'Stop'

$BackendRoot = 'D:\sanmitra-backend'
$LegalMitraRoot = 'D:\sanmitra-backend\external-repos\LegalMitra'
$ConfigJsPath = Join-Path $LegalMitraRoot 'frontend\config.js'

function Ensure-ServiceRunning {
    param([string]$Name)

    $svc = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $svc) {
        Write-Host "[WARN] Service '$Name' not found. Skipping." -ForegroundColor Yellow
        return
    }

    if ($svc.Status -ne 'Running') {
        Write-Host "[INFO] Starting service: $Name" -ForegroundColor Cyan
        Start-Service -Name $Name
        $svc.WaitForStatus('Running', [TimeSpan]::FromSeconds(20))
    }

    Write-Host "[OK] Service running: $Name" -ForegroundColor Green
}

if (-not (Test-Path $BackendRoot)) {
    throw "Backend root not found: $BackendRoot"
}
if (-not (Test-Path $LegalMitraRoot)) {
    throw "LegalMitra root not found: $LegalMitraRoot"
}
if (-not (Test-Path $ConfigJsPath)) {
    throw "Frontend config.js not found: $ConfigJsPath"
}

if (-not $SkipServiceStart) {
    Ensure-ServiceRunning -Name 'postgresql-x64-16'
    Ensure-ServiceRunning -Name 'MongoDB'
}

# Point LegalMitra frontend to unified backend.
$config = Get-Content -LiteralPath $ConfigJsPath -Raw
$targetApi = "http://localhost:$BackendPort/api/v1"
if ($config -match 'http://localhost:8888/api/v1') {
    $config = [Regex]::Replace($config, 'http://localhost:\d+/api/v1', $targetApi)
} elseif ($config -match 'http://localhost:\d+/api/v1') {
    $config = [Regex]::Replace($config, 'http://localhost:\d+/api/v1', $targetApi)
}
Set-Content -LiteralPath $ConfigJsPath -Value $config
Write-Host "[OK] Frontend API_BASE_URL set to $targetApi" -ForegroundColor Green

$backendCmd = "Set-Location '$BackendRoot'; .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --host 127.0.0.1 --port $BackendPort --reload"
$frontendCmd = "Set-Location '$LegalMitraRoot'; python start_frontend.py"

Start-Process powershell -ArgumentList '-NoExit', '-Command', $backendCmd | Out-Null
Start-Process powershell -ArgumentList '-NoExit', '-Command', $frontendCmd | Out-Null

Write-Host ''
Write-Host 'Started:' -ForegroundColor Cyan
Write-Host "- Backend docs: http://127.0.0.1:$BackendPort/docs"
Write-Host "- Backend health: http://127.0.0.1:$BackendPort/health"
Write-Host '- LegalMitra frontend: http://localhost:3005'
Write-Host ''
Write-Host "Tip: run with -SkipServiceStart if DB services are already running." -ForegroundColor DarkGray

