# Quick-start NapCat QQ (uses saved login state, no QR scan needed)
param([int]$QQNumber = 1534055688)

$ErrorActionPreference = "Continue"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Base

Write-Host "=== NapCat QQ (Quick) ===" -ForegroundColor Cyan

$QQExe = Join-Path $Base "NapCat.44498.Shell\QQ.exe"
if (-not (Test-Path $QQExe)) {
    Write-Host "[!] QQ not found. Run setup-napcat.ps1 first." -ForegroundColor Red
    exit 1
}

$QQRunning = Get-Process -Name "QQ" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "*localqq*" }

if ($QQRunning) {
    Write-Host "[OK] QQ already running (PID: $($QQRunning.Id))" -ForegroundColor Green
} else {
    Write-Host "[..] Quick-starting QQ (saved login)..." -ForegroundColor Yellow

    # Use `-q <QQ号>` for quick login if previously logged in
    $NapCatDir = Join-Path $Base "NapCat.44498.Shell\versions\9.9.26-44498\resources\app\napcat"
    $env:NAPCAT_PATCH_PACKAGE = Join-Path $NapCatDir "qqnt.json"
    $env:NAPCAT_LOAD_PATH = Join-Path $NapCatDir "loadNapCat.js"
    $env:NAPCAT_INJECT_PATH = Join-Path $NapCatDir "NapCatWinBootHook.dll"
    $env:NAPCAT_LAUNCHER_PATH = Join-Path $NapCatDir "NapCatWinBootMain.exe"
    $env:NAPCAT_MAIN_PATH = Join-Path $NapCatDir "napcat.mjs"

    $mjsFwd = $env:NAPCAT_MAIN_PATH -replace '\\', '/'
    "(async () => {await import('file:///$mjsFwd')})()" | Out-File -FilePath $env:NAPCAT_LOAD_PATH -Encoding UTF8

    # Quick login: pass -q <QQ号> to skip QR
    $argList = @($QQExe, $env:NAPCAT_INJECT_PATH, "-q", $QQNumber)
    Start-Process -FilePath $env:NAPCAT_LAUNCHER_PATH -ArgumentList $argList -WorkingDirectory $NapCatDir
}

# Wait for WebSocket
Write-Host "[..] Waiting for NapCat..." -ForegroundColor Yellow
$wsReady = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    if (netstat -ano 2>$null | Select-String "LISTENING.*3001") {
        $wsReady = $true
        break
    }
}

if ($wsReady) {
    Write-Host "NapCat ready: ws://127.0.0.1:3001" -ForegroundColor Green
} else {
    Write-Host "Not up yet - maybe need login first. Try: .\start.ps1" -ForegroundColor DarkYellow
}
