param(
    [switch]$ValidateEncoding
)

$ErrorActionPreference = "Stop"

if ($ValidateEncoding) {
    Write-Host "Windows PowerShell script encoding validation passed."
    exit 0
}

$AppHome = Split-Path -Parent $PSScriptRoot
$Executable = Join-Path $AppHome "EcommerceAgent.exe"
$ExampleEnv = Join-Path $AppHome "config\.env.example"
$EnvFile = Join-Path $AppHome ".env"

function Set-EnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )
    $Lines = @(Get-Content -LiteralPath $EnvFile -Encoding UTF8)
    $Replacement = "$Name=$Value"
    $Found = $false
    $Updated = foreach ($Line in $Lines) {
        if ($Line -match "^$([regex]::Escape($Name))=") {
            $Found = $true
            $Replacement
        } else {
            $Line
        }
    }
    if (-not $Found) {
        $Updated += $Replacement
    }
    Set-Content -LiteralPath $EnvFile -Value $Updated -Encoding UTF8
}

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "此发布包仅支持 Windows 10/11 x64。"
}
if ([Environment]::OSVersion.Version.Major -lt 10) {
    throw "此发布包仅支持 Windows 10/11 x64。"
}
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "发布包不完整：未找到 EcommerceAgent.exe。"
}
if (-not (Test-Path -LiteralPath $ExampleEnv -PathType Leaf)) {
    throw "发布包不完整：未找到 config\.env.example。"
}

foreach ($Directory in @(
    "storage",
    "storage\backups",
    "storage\exports",
    "storage\mailing-media",
    "logs",
    "temporary",
    "temporary\creator-imports",
    "temporary\ziniao-webdrivers",
    "temporary\ziniao-browser-sessions"
)) {
    New-Item -ItemType Directory -Path (Join-Path $AppHome $Directory) -Force |
        Out-Null
}

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    Copy-Item -LiteralPath $ExampleEnv -Destination $EnvFile
    $SecretBytes = New-Object byte[] 48
    $RandomGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $RandomGenerator.GetBytes($SecretBytes)
    } finally {
        $RandomGenerator.Dispose()
    }
    $Secret = [Convert]::ToBase64String($SecretBytes)
    Set-EnvValue -Name "DJANGO_SECRET_KEY" -Value $Secret
    Write-Host "已创建本机 .env 并生成随机 Django 密钥。"
} else {
    Write-Host "检测到已有 .env，已保留且未覆盖。"
}

$DefaultZiniao = "C:\Program Files\ziniao\ziniao.exe"
$CurrentZiniao = (
    Get-Content -LiteralPath $EnvFile -Encoding UTF8 |
        Where-Object { $_ -match '^ZINIAO_CLIENT_PATH=' } |
        Select-Object -First 1
) -replace '^ZINIAO_CLIENT_PATH=', ''
if (-not $CurrentZiniao -and (Test-Path -LiteralPath $DefaultZiniao)) {
    Set-EnvValue -Name "ZINIAO_CLIENT_PATH" -Value $DefaultZiniao
    $CurrentZiniao = $DefaultZiniao
}
if (-not $CurrentZiniao -or -not (Test-Path -LiteralPath $CurrentZiniao)) {
    Write-Host "请输入本机 ziniao.exe 完整路径；可暂时留空，稍后编辑 .env。"
    $EnteredZiniao = (Read-Host "ziniao.exe 路径").Trim('"').Trim()
    if ($EnteredZiniao) {
        if (-not (Test-Path -LiteralPath $EnteredZiniao -PathType Leaf)) {
            throw "指定的 ziniao.exe 不存在：$EnteredZiniao"
        }
        Set-EnvValue -Name "ZINIAO_CLIENT_PATH" -Value $EnteredZiniao
    }
}

$OpenCode = Join-Path $AppHome "runtime\opencode\opencode.exe"
if (-not (Test-Path -LiteralPath $OpenCode -PathType Leaf)) {
    throw "发布包不完整：未找到已校验的 OpenCode 运行时。"
}
& $OpenCode --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "OpenCode 运行时检查失败，退出代码：$LASTEXITCODE"
}

Write-Host "即将打开本机配置文件。请填写紫鸟、DeepSeek 和 Gmail 配置后保存关闭。"
Start-Process -FilePath "notepad.exe" -ArgumentList "`"$EnvFile`"" -Wait

& $Executable --internal-manage migrate --noinput
if ($LASTEXITCODE -ne 0) {
    throw "数据库初始化失败，退出代码：$LASTEXITCODE"
}
& $Executable --internal-manage check
if ($LASTEXITCODE -ne 0) {
    throw "系统检查失败，退出代码：$LASTEXITCODE"
}

Write-Host ""
Write-Host "首次配置完成。配置文件和业务数据仅保存在：$AppHome"
Write-Host "未输出任何密码或 API 密钥。现在可以运行 启动系统.cmd。"
