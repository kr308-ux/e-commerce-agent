$ErrorActionPreference = "Stop"

$AppHome = Split-Path -Parent $PSScriptRoot
$Executable = (Join-Path $AppHome "EcommerceAgent.exe").ToLowerInvariant()
$Processes = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.ToLowerInvariant() -eq $Executable
}

if (-not $Processes) {
    Write-Host "系统当前未运行。"
    exit 0
}

foreach ($Process in $Processes) {
    Stop-Process -Id $Process.ProcessId -Force
}
Write-Host "已停止 EcommerceAgent；其受管子进程会由 Windows 作业对象一并清理。"
