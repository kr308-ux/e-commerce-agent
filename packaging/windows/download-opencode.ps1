param(
    [string]$Version = "1.18.10"
)

$ErrorActionPreference = "Stop"

$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $RepositoryRoot "build\opencode"
$Archive = Join-Path $BuildRoot "opencode-windows-x64.zip"
$Extracted = Join-Path $BuildRoot "extracted"
$ExpectedSha256 = "b1d85ce5211bfefbc2b4940a19e1639fc75cb87ff82eb79806ffb84b01dd1482"
$DownloadUrl = "https://github.com/anomalyco/opencode/releases/download/v$Version/opencode-windows-x64.zip"

if ($Version -ne "1.18.10") {
    throw "OpenCode 版本已固定为 1.18.10；升级时必须同时审核并更新 SHA-256。"
}

New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
    Invoke-WebRequest -Uri $DownloadUrl -OutFile $Archive
}
$ActualSha256 = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualSha256 -ne $ExpectedSha256) {
    throw "OpenCode SHA-256 校验失败。期望 $ExpectedSha256，实际 $ActualSha256。"
}

if (Test-Path -LiteralPath $Extracted) {
    Remove-Item -LiteralPath $Extracted -Recurse -Force
}
Expand-Archive -LiteralPath $Archive -DestinationPath $Extracted
$Executable = Get-ChildItem -LiteralPath $Extracted -Filter "opencode.exe" -File -Recurse |
    Select-Object -First 1
if (-not $Executable) {
    throw "官方 OpenCode ZIP 中未找到 opencode.exe。"
}

Write-Output $Executable.FullName
