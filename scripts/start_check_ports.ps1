param(
    [switch]$StartBackendIfNeeded = $true,
    [int]$PortWaitSeconds = 90
)

$ErrorActionPreference = 'Stop'

function Test-PortListening {
    param([int]$Port)
    try {
        $conns = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop
        return ($conns | Measure-Object).Count -gt 0
    }
    catch {
        $net = netstat -ano | Select-String ":$Port"
        return ($net | Select-String 'LISTENING') -ne $null
    }
}

function Test-Http {
    param([string]$Url, [int]$TimeoutSec = 8)
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return [pscustomobject]@{ ok = $true; status = [int]$resp.StatusCode; error = $null }
    }
    catch {
        $status = $null
        try {
            if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
                $status = [int]$_.Exception.Response.StatusCode.value__
            }
        }
        catch {}
        return [pscustomobject]@{ ok = $false; status = $status; error = $_.Exception.Message }
    }
}

function Test-FrontendReady {
    param(
        [int]$Port,
        [string]$Path = '/',
        [int]$Attempts = 18,
        [int]$TimeoutSecPerTry = 10,
        [int]$SleepSec = 3
    )

    $normalizedPath = if ([string]::IsNullOrWhiteSpace($Path)) { '/' } elseif ($Path.StartsWith('/')) { $Path } else { '/' + $Path }
    $bases = @("http://localhost:$Port", "http://127.0.0.1:$Port")
    $last = [pscustomobject]@{ ok = $false; status = $null; error = 'No HTTP probe attempted'; url = $null }

    for ($i = 1; $i -le $Attempts; $i++) {
        foreach ($base in $bases) {
            $url = "$base$normalizedPath"
            $probe = Test-Http -Url $url -TimeoutSec $TimeoutSecPerTry

            if ($probe.ok) {
                return [pscustomobject]@{ ok = $true; status = $probe.status; error = $null; url = $url }
            }

            if ($probe.status -ne $null) {
                return [pscustomobject]@{ ok = $true; status = $probe.status; error = $probe.error; url = $url }
            }

            $last = [pscustomobject]@{ ok = $false; status = $probe.status; error = $probe.error; url = $url }
        }

        Start-Sleep -Seconds $SleepSec
    }

    return $last
}

function Wait-ForPort {
    param([int]$Port, [int]$TimeoutSec = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortListening -Port $Port) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Start-AppProcess {
    param([string]$Name, [string]$WorkingDir, [string]$Command, [string]$LogDir)
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $outLog = Join-Path $LogDir ("$Name.stdout.log")
    $errLog = Join-Path $LogDir ("$Name.stderr.log")
    $proc = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $Command -WorkingDirectory $WorkingDir -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
    return [pscustomobject]@{ Process = $proc; StdOut = $outLog; StdErr = $errLog }
}

function Stop-AppProcessTree {
    param([int]$ProcessId)
    try {
        taskkill /PID $ProcessId /T /F | Out-Null
    }
    catch {}
}

$root = (Get-Location).Path
$logDir = Join-Path $root 'logs\start-check'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$backendStarted = $false
$backendProc = $null

if (-not (Test-PortListening -Port 8000)) {
    if ($StartBackendIfNeeded) {
        $backendStart = Start-AppProcess -Name 'backend_8000' -WorkingDir $root -Command 'python -m uvicorn app.main:app --host 0.0.0.0 --port 8000' -LogDir $logDir
        $backendProc = $backendStart.Process
        $backendStarted = $true
        [void](Wait-ForPort -Port 8000 -TimeoutSec 60)
    }
}

$backendHealth = Test-Http -Url 'http://127.0.0.1:8000/health' -TimeoutSec 10

$apps = @(
    [pscustomobject]@{ Name='LegalMitra';  Dir='external-repos/LegalMitra';         Cmd='python start_frontend.py'; Port=3000; CheckPath='/';      UiPath='/' },
    [pscustomobject]@{ Name='GruhaMitra';  Dir='external-repos/GharMitra/web';       Cmd='npm run dev';              Port=3100; CheckPath='/login'; UiPath='/login' },
    [pscustomobject]@{ Name='MandirMitra'; Dir='external-repos/MandirMitra/frontend'; Cmd='npm start';                Port=3200; CheckPath='/login'; UiPath='/login' },
    [pscustomobject]@{ Name='MitraBooks';  Dir='external-repos/MitraBooks/frontend';  Cmd='npm run dev';              Port=3300; CheckPath='/login'; UiPath='/login' },
    [pscustomobject]@{ Name='InvestMitra'; Dir='external-repos/InvestMitra/frontend'; Cmd='npm start';                Port=3400; CheckPath='/';      UiPath='/#/auth' }
)

$results = @()

foreach ($app in $apps) {
    $dir = Join-Path $root $app.Dir
    $portAlready = Test-PortListening -Port $app.Port
    $startedByScript = $false
    $procInfo = $null
    $portUp = $false

    if (-not $portAlready) {
        try {
            $procInfo = Start-AppProcess -Name $app.Name -WorkingDir $dir -Command $app.Cmd -LogDir $logDir
            $startedByScript = $true
            $portUp = Wait-ForPort -Port $app.Port -TimeoutSec $PortWaitSeconds
        }
        catch {
            $portUp = $false
        }
    }
    else {
        $portUp = $true
    }

    $frontHttp = Test-FrontendReady -Port $app.Port -Path $app.CheckPath -Attempts 18 -TimeoutSecPerTry 10 -SleepSec 3
    $backendNow = Test-Http -Url 'http://127.0.0.1:8000/health' -TimeoutSec 10

    $results += [pscustomobject]@{
        app = $app.Name
        expected_port = $app.Port
        frontend_ui_url = "http://localhost:$($app.Port)$($app.UiPath)"
        frontend_check_path = $app.CheckPath
        port_already_listening = $portAlready
        started_by_script = $startedByScript
        port_listening_after = $portUp
        frontend_http_ok = $frontHttp.ok
        frontend_http_status = $frontHttp.status
        frontend_http_error = $frontHttp.error
        frontend_probe_url = $frontHttp.url
        backend_8000_ok = $backendNow.ok
        backend_8000_status = $backendNow.status
        backend_8000_error = $backendNow.error
        stdout_log = $(if($procInfo){$procInfo.StdOut}else{$null})
        stderr_log = $(if($procInfo){$procInfo.StdErr}else{$null})
    }

    if ($startedByScript -and $procInfo -and $procInfo.Process) {
        Stop-AppProcessTree -ProcessId $procInfo.Process.Id
    }
}

if ($backendStarted -and $backendProc) {
    Stop-AppProcessTree -ProcessId $backendProc.Id
}

$reportPath = Join-Path $logDir 'start-check-report.json'
$results | ConvertTo-Json -Depth 6 | Set-Content -Path $reportPath

Write-Output ('BACKEND_HEALTH_OK=' + $backendHealth.ok + ' STATUS=' + $backendHealth.status)
Write-Output ('REPORT=' + $reportPath)
$results | Format-Table -AutoSize | Out-String | Write-Output
