$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ManagePy = Join-Path $ProjectRoot "web-ui\manage.py"
$ChildProcesses = @()

try {
    $RunserverArguments = @($ManagePy, "runserver") + $args
    $DjangoProcess = Start-Process `
        -FilePath $Python `
        -ArgumentList $RunserverArguments `
        -NoNewWindow `
        -PassThru
    $ChildProcesses += $DjangoProcess

    Write-Host "Django 已启动，正在预启动并验收紫鸟店铺浏览器首页。"
    & $Python $ManagePy prepare_ziniao_browser
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "紫鸟店铺首页暂未就绪；Django 和 Worker 将继续运行，请根据页面状态完成登录后重试。"
    }

    $ChildProcesses += Start-Process `
        -FilePath $Python `
        -ArgumentList @($ManagePy, "run_import_worker") `
        -NoNewWindow `
        -PassThru

    $ChildProcesses += Start-Process `
        -FilePath $Python `
        -ArgumentList @($ManagePy, "run_creator_contact_worker", "--server-mode") `
        -NoNewWindow `
        -PassThru

    $ChildProcesses += Start-Process `
        -FilePath $Python `
        -ArgumentList @($ManagePy, "run_collaboration_sync_worker") `
        -NoNewWindow `
        -PassThru

    Write-Host "Django、店铺浏览器、达人导入 Worker、达人联系 Worker 和定向合作同步 Worker 已就绪。"
    $DjangoProcess.WaitForExit()
    exit $DjangoProcess.ExitCode
}
finally {
    foreach ($Process in $ChildProcesses) {
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id
        }
    }
}
