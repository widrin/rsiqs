# RSIQS Windows 计划任务入口。用法（schtasks /TR 里调用）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File <root>\scripts\run_job.ps1 <job>[,<job>...]
# 例：run_job.ps1 settle,review —— 依次跑，前一个失败即停（保留退出码）。
# 先切到仓库根目录（schtasks 没有工作目录概念，配置/数据库都按相对路径解析）。
# 注意：不要给 param 加 [Parameter(Mandatory)] 之类的属性——powershell -File 调用时
# 属性化参数不按位置绑定（实测值会落到 $args，参数为空）。用简单 param + 手工判空。
param([string]$Jobs)

if (-not $Jobs) {
    Write-Error "usage: run_job.ps1 <job>[,<job>...]  例如 run_job.ps1 settle,review"
    exit 2
}
Set-Location $PSScriptRoot\..
# 计划任务以 SYSTEM 身份运行，若系统级代理（如 127.0.0.1:19828 的残留配置）已失效，
# requests 会读注册表代理并全部请求失败。这里强制直连，避免任务被无关的系统代理拖垮。
$env:NO_PROXY = '*'
$env:no_proxy = '*'
'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy' | ForEach-Object {
    Remove-Item "Env:$_" -ErrorAction SilentlyContinue
}
$python = ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "python venv not found: $python"
    exit 1
}
foreach ($j in $Jobs -split ',') {
    & $python -m "jobs.$($j.Trim())"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
