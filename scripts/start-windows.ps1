$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ManagePy = Join-Path $ProjectRoot "web-ui\manage.py"

Write-Host "正在启动系统守护程序。按 Ctrl+C 可安全停止。"
& $Python $ManagePy run_runtime_supervisor @args
exit $LASTEXITCODE
