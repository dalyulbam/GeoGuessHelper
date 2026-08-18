# ALTAIYA — 백엔드(8901) + 프론트엔드(8902) 동시 실행, 브라우저 자동 오픈
# 사용: 우클릭 → PowerShell로 실행, 또는 run.bat 더블클릭
$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path      # ...\altaiya
$root = Split-Path -Parent $here                             # E:\GeoguessHelper (uv 프로젝트)
$apiPort = if ($env:ALTAIYA_API_PORT) { $env:ALTAIYA_API_PORT } else { 8901 }
$webPort = if ($env:ALTAIYA_WEB_PORT) { $env:ALTAIYA_WEB_PORT } else { 8902 }

Write-Host ""
Write-Host "  == ALTAIYA launcher =============================" -ForegroundColor Cyan
Write-Host "   backend  : http://localhost:$apiPort  (지식 그래프 API)"
Write-Host "   frontend : http://localhost:$webPort  (전략 지도)"
Write-Host "  ================================================="
Write-Host ""

# 이미 떠 있으면 창만 다시 연다 (중복 실행 방지)
function Test-Port([int]$p) {
    try { (New-Object Net.Sockets.TcpClient("127.0.0.1", $p)).Close(); $true } catch { $false }
}

if (-not (Test-Port $apiPort)) {
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-Command",
        "`$host.UI.RawUI.WindowTitle = 'ALTAIYA backend :$apiPort'; " +
        "`$env:ALTAIYA_API_PORT = '$apiPort'; " +
        "Set-Location '$root'; uv run python '$here\altaiya-backend\app.py'"
    )
} else {
    Write-Host "   backend $apiPort 이미 실행 중 - 재사용" -ForegroundColor DarkYellow
}

if (-not (Test-Port $webPort)) {
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-Command",
        "`$host.UI.RawUI.WindowTitle = 'ALTAIYA frontend :$webPort'; " +
        "Set-Location '$root'; uv run python -m http.server $webPort --bind 127.0.0.1 --directory '$here\altaiya-frontend'"
    )
} else {
    Write-Host "   frontend $webPort 이미 실행 중 - 재사용" -ForegroundColor DarkYellow
}

# 백엔드가 뜰 때까지 잠깐 기다렸다가 브라우저를 연다 (최대 15초)
$deadline = (Get-Date).AddSeconds(15)
while (-not (Test-Port $webPort) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
while (-not (Test-Port $apiPort) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }

Start-Process "http://localhost:$webPort/"
Write-Host "   브라우저를 열었습니다. 서버 창을 닫으면 종료됩니다." -ForegroundColor Green
