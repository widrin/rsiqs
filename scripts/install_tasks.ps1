# Register RSIQS scheduled tasks for the current user (run only while logged on).
# Usage (repo root): powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_tasks.ps1
# Re-running is safe: -Force overwrites existing task definitions.
# PremarketFinal fires at 09:25; the job itself waits until 09:25:15 for auction data.
# Settle and Review repeat every 20 minutes for 5 hours: a failed or not-yet-ready run is retried the same
# evening (jobs are idempotent and exit at once when already done). Missed Settle/Review runs after boot are
# handled by Catchup, so only the morning tasks use StartWhenAvailable; a missed Saturday Research is skipped.
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Uv = (Get-Command uv -ErrorAction Stop).Source
$Folder = "\RSIQS\"
$User = "$env:USERDOMAIN\$env:USERNAME"
$Weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

function New-JobAction([string]$Job) {
    New-ScheduledTaskAction -Execute $Uv -WorkingDirectory $Root `
        -Argument "run --project `"$Root`" python -m jobs.$Job"
}

function New-WeekdayTrigger([string]$At) {
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Weekdays -At $At
}

function New-RetryTrigger([string]$At) {
    $t = New-WeekdayTrigger $At
    $t.Repetition = (New-ScheduledTaskTrigger -Once -At $At -RepetitionInterval (New-TimeSpan -Minutes 20) `
        -RepetitionDuration (New-TimeSpan -Hours 5)).Repetition
    $t
}

function Register-Rsiqs([string]$Name, $Trigger, $Action, [bool]$Available = $false) {
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable:$Available -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive
    Register-ScheduledTask -TaskPath $Folder -TaskName $Name -Trigger $Trigger -Action $Action `
        -Settings $settings -Principal $principal -Description "RSIQS $Name" -Force | Out-Null
    Write-Host "registered $Folder$Name"
}

Register-Rsiqs "PremarketScan"  (New-WeekdayTrigger "08:30") (New-JobAction "premarket_scan") $true
Register-Rsiqs "PremarketFinal" (New-WeekdayTrigger "09:25") (New-JobAction "premarket_final") $true
Register-Rsiqs "Monitor"        (New-WeekdayTrigger "09:30") (New-JobAction "monitor") $true
Register-Rsiqs "Settle"         (New-RetryTrigger "15:35") (New-JobAction "settle")
Register-Rsiqs "Review"         (New-RetryTrigger "16:05") (New-JobAction "review")
Register-Rsiqs "Research" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At "10:00") `
    (New-JobAction "research")

# Catch-up after boot/logon: settle first, then review (actions run sequentially).
$logon = New-ScheduledTaskTrigger -AtLogOn -User $User
$logon.Delay = "PT3M"
Register-Rsiqs "Catchup" $logon @((New-JobAction "settle"), (New-JobAction "review"))

Get-ScheduledTask -TaskPath $Folder | Select-Object TaskName, State | Format-Table -AutoSize
