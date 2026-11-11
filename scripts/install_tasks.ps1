# Register the 9 RSIQS scheduled tasks exactly as documented in README (see the scheduled-tasks table):
# root path "\", RSIQS_ prefix, SYSTEM identity, every action goes through scripts\run_job.ps1
# (repo venv python, no uv needed on PATH). Requires an ELEVATED PowerShell (SYSTEM registration).
# Usage (repo root): powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_tasks.ps1
# Re-running is safe: -Force overwrites existing definitions.
# Triggers (non-trading days are skipped by the jobs themselves):
#   PremarketScan  weekdays 08:30 (wake from sleep)      PremarketFinal weekdays 09:25 (wake)
#   Monitor        daily 09:30 + every 2 min for 5h30m   Midday  weekdays 11:35
#   Settle daily 15:35 + every 20 min for 2h55m
#   Review         daily 16:05 + every 20 min for 2h35m  Doctor daily 07:30
#   Research       Saturday 10:00 (missed run is skipped by the job)
#   BootCatchup    at system start: settle then review (actions run sequentially)
# Overlapping runs are serialized by the per-job file lock in jobs.common.
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Runner = Join-Path $Root "scripts\run_job.ps1"

function New-JobAction([string]$Jobs) {
    New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $Root `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" $Jobs"
}

function New-WeekdayTrigger([string]$At) {
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $At
}

function New-RetryTrigger([string]$At, [int]$EveryMin, [int]$ForMin) {
    # Daily trigger + repetition; Repetition is built on a -Once trigger and copied over
    # (PS 5.1 cannot set Repetition directly on a daily trigger).
    $t = New-ScheduledTaskTrigger -Daily -At $At
    $r = (New-ScheduledTaskTrigger -Once -At $At -RepetitionInterval (New-TimeSpan -Minutes $EveryMin) `
        -RepetitionDuration (New-TimeSpan -Minutes $ForMin)).Repetition
    $r.StopAtDurationEnd = $false
    $t.Repetition = $r
    $t
}

function Register-Rsiqs([string]$Name, $Trigger, $Action, [bool]$Available = $false, [bool]$Wake = $false) {
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable:$Available -WakeToRun:$Wake `
        -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount
    Register-ScheduledTask -TaskName "RSIQS_$Name" -Trigger $Trigger -Action $Action `
        -Settings $settings -Principal $principal -Description "RSIQS $Name" -Force | Out-Null
    Write-Host "registered \RSIQS_$Name"
}

Register-Rsiqs "premarket_scan"  (New-WeekdayTrigger "08:30") (New-JobAction "premarket_scan") $true $true
Register-Rsiqs "premarket_final" (New-WeekdayTrigger "09:25") (New-JobAction "premarket_final") $true $true
Register-Rsiqs "monitor"         (New-RetryTrigger "09:30" 2 330)  (New-JobAction "monitor")
Register-Rsiqs "midday"          (New-WeekdayTrigger "11:35")      (New-JobAction "midday") $true $false
Register-Rsiqs "settle"          (New-RetryTrigger "15:35" 20 175) (New-JobAction "settle")
Register-Rsiqs "review"          (New-RetryTrigger "16:05" 20 155) (New-JobAction "review")
Register-Rsiqs "doctor"          (New-ScheduledTaskTrigger -Daily -At "07:30") (New-JobAction "doctor")
Register-Rsiqs "research"        (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At "10:00") `
                                 (New-JobAction "research")
Register-Rsiqs "boot_catchup"    (New-ScheduledTaskTrigger -AtStartup) (New-JobAction "settle,review")

Get-ScheduledTask | Where-Object TaskName -Like 'RSIQS_*' |
    Select-Object TaskName, State | Format-Table -AutoSize
