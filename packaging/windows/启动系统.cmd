@echo off
chcp 65001 >nul
set "APP_HOME=%~dp0"
if not exist "%APP_HOME%.env" (
  echo 尚未完成首次配置，请先运行 首次配置.cmd。
  pause
  exit /b 1
)
echo 正在启动系统。访问地址：http://127.0.0.1:8000/
echo 按 Ctrl+C 可安全停止所有受管服务。
"%APP_HOME%EcommerceAgent.exe"
set "EXIT_CODE=%ERRORLEVEL%"
echo 系统已退出，代码：%EXIT_CODE%
pause
exit /b %EXIT_CODE%
