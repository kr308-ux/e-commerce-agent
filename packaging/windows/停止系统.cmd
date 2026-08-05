@echo off
chcp 65001 >nul
set "APP_HOME=%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%APP_HOME%tools\stop-system.ps1"
if errorlevel 1 (
  echo 停止失败，请尝试在运行窗口按 Ctrl+C。
  pause
  exit /b 1
)
pause
