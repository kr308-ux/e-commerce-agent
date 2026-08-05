$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

py -3 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

if (Select-String -Path ".env" -Pattern "ZINIAO_CLIENT_PATH=/Applications/ziniao.app" -Quiet) {
    Write-Warning ".env 仍包含 macOS 紫鸟路径，请手动清空 ZINIAO_CLIENT_PATH 或填写实际 ziniao.exe 路径。"
}

& ".\.venv\Scripts\python.exe" "web-ui\manage.py" check
& ".\.venv\Scripts\python.exe" "web-ui\manage.py" migrate

Write-Host "环境准备完成。可双击 scripts\start-windows.cmd 启动全部服务。"
