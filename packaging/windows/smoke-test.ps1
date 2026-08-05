$ErrorActionPreference = "Stop"

$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuiltApplication = Join-Path $RepositoryRoot "dist\EcommerceAgent"
if (-not (Test-Path -LiteralPath (Join-Path $BuiltApplication "EcommerceAgent.exe"))) {
    throw "未找到 PyInstaller Windows 构建结果。"
}

$SmokeRoot = Join-Path $RepositoryRoot "build\windows-smoke"
if (Test-Path -LiteralPath $SmokeRoot) {
    Remove-Item -LiteralPath $SmokeRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $SmokeRoot -Force | Out-Null
Copy-Item -LiteralPath $BuiltApplication -Destination $SmokeRoot -Recurse
$ApplicationHome = Join-Path $SmokeRoot "EcommerceAgent"
$Executable = Join-Path $ApplicationHome "EcommerceAgent.exe"

$env:DJANGO_SECRET_KEY = "ci-smoke-only-not-a-business-secret"
$env:DJANGO_DEBUG = "true"
$env:DATABASE_PATH = "storage/smoke.db"
$env:RUNTIME_LOG_ROOT = "logs"
$env:IMPORT_TEMP_DIR = "temporary/creator-imports"
$env:ZINIAO_BROWSER_STATUS_PATH = "temporary/ziniao-browser-status.json"
$env:ZINIAO_ADAPTIVE_LOCATOR_PATH = "temporary/ziniao-adaptive-locators.sqlite3"
$env:MAILING_MEDIA_ROOT = "storage/mailing-media"

try {
    & $Executable --internal-manage migrate --noinput
    if ($LASTEXITCODE -ne 0) {
        throw "冻结程序 migrate smoke test 失败：$LASTEXITCODE"
    }
    & $Executable --internal-manage check
    if ($LASTEXITCODE -ne 0) {
        throw "冻结程序 check smoke test 失败：$LASTEXITCODE"
    }
    & $Executable --skip-browser-prepare --once
    if ($LASTEXITCODE -ne 0) {
        throw "冻结 Supervisor smoke test 失败：$LASTEXITCODE"
    }
} finally {
    if (Test-Path -LiteralPath $SmokeRoot) {
        Remove-Item -LiteralPath $SmokeRoot -Recurse -Force
    }
}
