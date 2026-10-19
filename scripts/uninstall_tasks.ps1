# Remove all RSIQS scheduled tasks (stops running instances first).
# Usage (repo root): powershell -NoProfile -ExecutionPolicy Bypass -File scripts\uninstall_tasks.ps1
$ErrorActionPreference = "Stop"
$Folder = "\RSIQS\"
$tasks = @(Get-ScheduledTask -TaskPath $Folder -ErrorAction SilentlyContinue)
foreach ($t in $tasks) {
    Stop-ScheduledTask -TaskPath $Folder -TaskName $t.TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskPath $Folder -TaskName $t.TaskName -Confirm:$false
    Write-Host "removed $Folder$($t.TaskName)"
}
Write-Host "RSIQS tasks removed: $($tasks.Count)"
