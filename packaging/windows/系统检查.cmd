@echo off
chcp 65001 >nul
set "APP_HOME=%~dp0"
if not exist "%APP_HOME%EcommerceAgent.exe" (
  echo 发布包不完整：未找到 EcommerceAgent.exe。
  pause
  exit /b 1
)
"%APP_HOME%EcommerceAgent.exe" --internal-manage check
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" echo 系统检查通过。
pause
exit /b %EXIT_CODE%
