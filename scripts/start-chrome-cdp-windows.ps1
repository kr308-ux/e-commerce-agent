$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProfileDirectory = Join-Path $ProjectRoot "temporary\chrome-cdp-profile"
$TargetUrl = "https://www.chuhaijiang.com/app/discover/tiktok/products?country=US"
$CdpPort = if ($env:CHROME_CDP_PORT) { $env:CHROME_CDP_PORT } else { "9333" }
$ChromeCandidates = @()
foreach ($BaseDirectory in @(
    $env:ProgramFiles,
    ${env:ProgramFiles(x86)},
    $env:LOCALAPPDATA
)) {
    if ($BaseDirectory) {
        $ChromeCandidates += Join-Path $BaseDirectory "Google\Chrome\Application\chrome.exe"
    }
}
$ChromeBinary = $ChromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $ChromeBinary) {
    throw "未找到 Google Chrome，请先安装 Chrome。"
}

New-Item -ItemType Directory -Force -Path $ProfileDirectory | Out-Null
Start-Process -FilePath $ChromeBinary -ArgumentList @(
    "--remote-debugging-port=$CdpPort",
    "--user-data-dir=$ProfileDirectory",
    $TargetUrl
)

for ($Attempt = 1; $Attempt -le 15; $Attempt++) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$CdpPort/json/version" -TimeoutSec 1 | Out-Null
        Write-Host "Chrome CDP 已启动：http://127.0.0.1:$CdpPort"
        exit 0
    }
    catch {
        Start-Sleep -Seconds 1
    }
}

throw "Chrome CDP 启动超时。"
