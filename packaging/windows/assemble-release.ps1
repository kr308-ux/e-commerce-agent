param(
    [Parameter(Mandatory = $true)][string]$Version
)

$ErrorActionPreference = "Stop"

$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuiltApplication = Join-Path $RepositoryRoot "dist\EcommerceAgent"
$ReleaseRoot = Join-Path $RepositoryRoot "release"
$StageRoot = Join-Path $ReleaseRoot "stage"
$ApplicationHome = Join-Path $StageRoot "EcommerceAgent"
$SafeVersion = $Version -replace '[^0-9A-Za-z._-]', '-'
$ZipName = "EcommerceAgent-Windows-x64-$SafeVersion.zip"
$ZipPath = Join-Path $ReleaseRoot $ZipName

if (-not (Test-Path -LiteralPath (Join-Path $BuiltApplication "EcommerceAgent.exe"))) {
    throw "未找到 dist\EcommerceAgent\EcommerceAgent.exe。"
}
if (-not $SafeVersion) {
    throw "发布版本不能为空。"
}

New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
if (Test-Path -LiteralPath $StageRoot) {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force
}
if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}

New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null
Copy-Item -LiteralPath $BuiltApplication -Destination $StageRoot -Recurse
foreach ($Directory in @(
    "config",
    "storage",
    "logs",
    "temporary",
    "runtime\opencode",
    "tools"
)) {
    New-Item -ItemType Directory -Path (Join-Path $ApplicationHome $Directory) -Force |
        Out-Null
}

Copy-Item -LiteralPath (Join-Path $RepositoryRoot ".env.example") `
    -Destination (Join-Path $ApplicationHome "config\.env.example")
foreach ($ScriptName in @(
    "首次配置.cmd",
    "启动系统.cmd",
    "停止系统.cmd",
    "系统检查.cmd"
)) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $ScriptName) `
        -Destination (Join-Path $ApplicationHome $ScriptName)
}
$Utf8WithBom = New-Object System.Text.UTF8Encoding($true)
$WindowsPowerShell = Join-Path `
    $env:SystemRoot `
    "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $WindowsPowerShell -PathType Leaf)) {
    throw "未找到 Windows PowerShell 5.1，无法验证客户机脚本兼容性。"
}
foreach ($ToolName in @("first-config.ps1", "stop-system.ps1")) {
    $ToolSource = Join-Path $PSScriptRoot $ToolName
    $ToolDestination = Join-Path $ApplicationHome "tools\$ToolName"
    $ToolContent = [IO.File]::ReadAllText($ToolSource, [Text.Encoding]::UTF8)
    [IO.File]::WriteAllText($ToolDestination, $ToolContent, $Utf8WithBom)

    & $WindowsPowerShell `
        -NoLogo `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File $ToolDestination `
        -ValidateEncoding
    if ($LASTEXITCODE -ne 0) {
        throw "Windows PowerShell 5.1 无法解析交付脚本：$ToolName"
    }
}

$OpenCodeExecutable = Get-ChildItem `
    -LiteralPath (Join-Path $RepositoryRoot "build\opencode\extracted") `
    -Filter "opencode.exe" -File -Recurse |
    Select-Object -First 1
if (-not $OpenCodeExecutable) {
    throw "未找到已校验的 OpenCode Windows x64 可执行文件。"
}
Copy-Item -LiteralPath $OpenCodeExecutable.FullName `
    -Destination (Join-Path $ApplicationHome "runtime\opencode\opencode.exe")
Copy-Item -LiteralPath (Join-Path $RepositoryRoot "packaging\third-party\opencode-LICENSE.txt") `
    -Destination (Join-Path $ApplicationHome "runtime\opencode\LICENSE.txt")

$Commit = (git -C $RepositoryRoot rev-parse HEAD).Trim()
$PythonVersion = (python --version 2>&1).Trim()
$PyInstallerVersion = (python -m PyInstaller --version 2>&1).Trim()
@(
    "EcommerceAgent Windows x64 one-folder release",
    "Version: $SafeVersion",
    "Git commit: $Commit",
    "Python: $PythonVersion",
    "PyInstaller: $PyInstallerVersion",
    "OpenCode: 1.18.10",
    "OpenCode archive SHA-256: b1d85ce5211bfefbc2b4940a19e1639fc75cb87ff82eb79806ffb84b01dd1482",
    "Built at UTC: $([DateTime]::UtcNow.ToString('o'))"
) | Set-Content -LiteralPath (Join-Path $ApplicationHome "BUILD-INFO.txt") -Encoding UTF8

$ForbiddenFiles = Get-ChildItem -LiteralPath $ApplicationHome -Recurse -Force | Where-Object {
    $RelativePath = $_.FullName.Substring($ApplicationHome.Length).TrimStart('\')
    $ProjectPythonSource = $_.Extension -eq ".py" -and (
        $RelativePath -match '^_internal\\(config|tasks|creator_contact|mailing|shared|ziniao_automation)\\' -or
        $RelativePath -eq '_internal\windows_entrypoint.py'
    )
    $ProjectPythonSource -or
    $_.Name -eq ".env" -or
    $_.Name -match '^agent\.db(?:-shm|-wal)?$' -or
    $_.Name -in @(".git", ".github", "tests", "__pycache__", ".venv", "node_modules")
}
if ($ForbiddenFiles) {
    $Names = ($ForbiddenFiles.FullName -join [Environment]::NewLine)
    throw "发布安全扫描发现禁止内容：`n$Names"
}

$TextFiles = Get-ChildItem -LiteralPath $ApplicationHome -Recurse -File | Where-Object {
    $_.Extension -in @(".txt", ".cmd", ".ps1", ".json", ".example")
}
$PathLeaks = $TextFiles | Select-String -SimpleMatch -Pattern @(
    "/Users/",
    "C:\Users\runneradmin\",
    $RepositoryRoot
)
if ($PathLeaks) {
    throw "发布文本中发现构建机绝对路径。"
}

Compress-Archive -LiteralPath $ApplicationHome -DestinationPath $ZipPath -CompressionLevel Optimal
$Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  $ZipName" | Set-Content -LiteralPath (Join-Path $ReleaseRoot "SHA256SUMS.txt") -Encoding ASCII

Write-Host "发布包已生成：$ZipPath"
Write-Host "SHA-256：$Hash"
