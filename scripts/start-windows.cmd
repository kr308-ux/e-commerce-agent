@echo off
setlocal
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start-windows.ps1"
if errorlevel 1 (
  echo.
  echo 系统启动失败，请把此窗口中的错误信息发给技术支持。
  pause
)
