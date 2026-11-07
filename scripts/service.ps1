# RSIQS service control: install / start / stop / remove / status.
# The whole system runs as 8 Windows scheduled tasks (root path "\", prefix RSIQS_, SYSTEM identity),
# so task operations need an ELEVATED PowerShell; status does not.
#
# Usage (repo root):
#   .\scripts\service.ps1 install [-Bootstrap]   # check venv/.env, register tasks, (optional) load data
#   .\scripts\service.ps1 start                  # enable all tasks
#   .\scripts\service.ps1 stop                   # stop running instances, then disable all tasks
#   .\scripts\service.ps1 remove                 # remove all tasks (data and logs are kept)
#   .\scripts\service.ps1 status                 # list state, last result and next run time
#
# Simple param without [Parameter(Mandatory)]: PS 5.1 -File binding does not honor attributes,
# so validate manually instead (same lesson as scripts\run_job.ps1).
param([string]$Action, [switch]$Bootstrap)

if (-not $Action) {
    Write-Error "usage: service.ps1 <install|start|stop|remove|status> [-Bootstrap]"
    exit 2
}
$Action = $Action.ToLower()
if ($Action -notin @("install", "start", "stop", "remove", "status")) {
    Write-Error "unknown action '$Action' (expected install, start, stop, remove or status)"
    exit 2
}

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$InstallScript = Join-Path $Root "scripts\install_tasks.ps1"
$UninstallScript = Join-Path $Root "scripts\uninstall_tasks.ps1"

function Get-RsiqsTasks {
    Get-ScheduledTask | Where-Object { $_.TaskPath -eq '\' -and $_.TaskName -like 'RSIQS_*' }
}

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Write-Error "Administrator privileges required. Open PowerShell as Administrator and retry."
        exit 1
    }
}

function Show-Status {
    $tasks = @(Get-RsiqsTasks)
    if ($tasks.Count -eq 0) {
        Write-Host "no RSIQS tasks registered (run: service.ps1 install)"
        return
    }
    $tasks | Sort-Object TaskName | ForEach-Object {
        $info = $_ | Get-ScheduledTaskInfo
        [pscustomobject]@{
            Task     = $_.TaskName
            State    = $_.State
            LastRun  = $info.LastRunTime
            Result   = $info.LastTaskResult
            NextRun  = $info.NextRunTime
        }
    } | Format-Table -AutoSize
}

function Invoke-Install {
    Assert-Admin

    Write-Host "[1/3] venv"
    if (-not (Test-Path $Python)) {
        Write-Host "  venv missing, running: uv sync"
        & uv sync
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Python)) {
            Write-Error "uv sync failed or venv still missing: $Python"
            exit 1
        }
    }
    Write-Host "  python: $Python"

    Write-Host "[2/3] .env"
    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path $envFile)) {
        Write-Error ".env not found. Copy .env.example to .env and fill LLM_BASE_URL / ARK_API_KEY."
        exit 1
    }
    $envText = Get-Content $envFile -Raw
    foreach ($key in @("LLM_BASE_URL", "ARK_API_KEY")) {
        if ($envText -notmatch "(?m)^\s*$key\s*=\s*\S") {
            Write-Error ".env is missing a non-empty $key (required, no default fallback)."
            exit 1
        }
    }
    if ($envText -notmatch "(?m)^\s*WECOM_WEBHOOK_URL\s*=\s*\S") {
        Write-Host "  warning: WECOM_WEBHOOK_URL empty, no push notifications will be sent"
    }
    Write-Host "  required keys present"

    if ($Bootstrap) {
        Write-Host "[bootstrap] loading historical data (about 1 hour, incremental on rerun)"
        & $Python -m jobs.bootstrap
        if ($LASTEXITCODE -ne 0) {
            Write-Error "bootstrap failed with exit code $LASTEXITCODE (rerun is incremental)"
            exit 1
        }
    }

    Write-Host "[3/3] registering 8 scheduled tasks"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $InstallScript
    if ($LASTEXITCODE -ne 0) {
        Write-Error "task registration failed with exit code $LASTEXITCODE"
        exit 1
    }
    Show-Status
}

function Invoke-Start {
    Assert-Admin
    $tasks = @(Get-RsiqsTasks)
    if ($tasks.Count -eq 0) {
        Write-Error "no RSIQS tasks registered (run: service.ps1 install)"
        exit 1
    }
    foreach ($t in $tasks) {
        Enable-ScheduledTask -TaskName $t.TaskName | Out-Null
        Write-Host "enabled $($t.TaskName)"
    }
}

function Invoke-Stop {
    Assert-Admin
    $tasks = @(Get-RsiqsTasks)
    if ($tasks.Count -eq 0) {
        Write-Host "no RSIQS tasks registered"
        return
    }
    foreach ($t in $tasks) {
        Stop-ScheduledTask -TaskName $t.TaskName -ErrorAction SilentlyContinue
        Disable-ScheduledTask -TaskName $t.TaskName | Out-Null
        Write-Host "disabled $($t.TaskName)"
    }
}

function Invoke-Remove {
    Assert-Admin
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $UninstallScript
    if ($LASTEXITCODE -ne 0) {
        Write-Error "task removal failed with exit code $LASTEXITCODE"
        exit 1
    }
}

switch ($Action) {
    "install" { Invoke-Install }
    "start"   { Invoke-Start }
    "stop"    { Invoke-Stop }
    "remove"  { Invoke-Remove }
    "status"  { Show-Status }
}
