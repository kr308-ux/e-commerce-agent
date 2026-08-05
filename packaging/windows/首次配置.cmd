@echo off
chcp 65001 >nul
set "APP_HOME=%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%APP_HOME%tools\first-config.ps1"
if errorlevel 1 (
  echo.
  echo 首次配置失败，请保留窗口中的错误信息。
  pause
  exit /b 1
)
pause
