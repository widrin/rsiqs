# Remove all RSIQS scheduled tasks (they live in the root path "\", prefix RSIQS_).
# Stops running instances first. Requires an ELEVATED PowerShell (SYSTEM tasks).
# Usage (repo root): powershell -NoProfile -ExecutionPolicy Bypass -File scripts\uninstall_tasks.ps1
$ErrorActionPreference = "Stop"
$tasks = @(Get-ScheduledTask | Where-Object { $_.TaskPath -eq '\' -and $_.TaskName -Like 'RSIQS_*' })
foreach ($t in $tasks) {
    Stop-ScheduledTask -TaskName $t.TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $t.TaskName -Confirm:$false
    Write-Host "removed \$($t.TaskName)"
}
Write-Host "RSIQS tasks removed: $($tasks.Count)"
