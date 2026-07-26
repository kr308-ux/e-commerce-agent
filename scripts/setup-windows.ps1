$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

py -3 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
npm install
npm run build

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

& ".\.venv\Scripts\python.exe" "web-ui\manage.py" check
& ".\.venv\Scripts\python.exe" "web-ui\manage.py" migrate

Write-Host "环境准备完成。分别运行 .\scripts\start-windows.ps1 和 .\scripts\start-worker-windows.ps1。"
