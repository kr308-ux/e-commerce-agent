$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

& ".\.venv\Scripts\python.exe" "web-ui\manage.py" run_creator_worker
