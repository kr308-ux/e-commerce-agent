$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

py -3 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

& ".\.venv\Scripts\python.exe" "web-ui\manage.py" check
& ".\.venv\Scripts\python.exe" "web-ui\manage.py" migrate

Write-Host "环境准备完成。运行 .\scripts\start-windows.ps1 可同时启动 Django、导入 Worker 和两个联系模块 Worker。"
