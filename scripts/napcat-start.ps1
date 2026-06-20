# Start NapCat QQ — injects NapCat into system-installed QQ
# Called by start-all.bat / start-quick.bat

$ErrorActionPreference = "Continue"
$Base = $PSScriptRoot
Set-Location (Split-Path $Base)

# NapCat injection files live in *_localqq
$NapCatLocal = Get-ChildItem -Directory -Path (Get-Location) -Filter "*_localqq" |
    Select-Object -First 1
if (-not $NapCatLocal) {
    Write-Host "[!] No *_localqq directory found. Run setup-napcat.ps1 first." -ForegroundColor Red
    exit 1
}
$NapCatLocal = $NapCatLocal.FullName

Write-Host "=== NapCat QQ ===" -ForegroundColor Cyan

# ---------- find system QQ ----------
$QQExe = $null
foreach ($p in @(
    "C:\Program Files\Tencent\QQNT\QQ.exe",
    "C:\Program Files (x86)\Tencent\QQNT\QQ.exe"
)) {
    if (Test-Path $p) { $QQExe = $p; break }
}
if (-not $QQExe) {
    Write-Host "[!] System QQ not found. Install QQ first." -ForegroundColor Red
    exit 1
}

# ---------- already up? ----------
if (netstat -ano 2>$null | Select-String "LISTENING.*3001") {
    Write-Host "[OK] NapCat already ready on port 3001" -ForegroundColor Green
    exit 0
}

# ---------- start / restart QQ ----------
$QQRunning = Get-Process -Name "QQ" -ErrorAction SilentlyContinue
if ($QQRunning) {
    Write-Host "[..] QQ running but NapCat not connected - restarting..." -ForegroundColor Yellow
    $QQRunning | Stop-Process -Force
    Start-Sleep -Seconds 3
}

Write-Host "[..] Starting QQ ($QQExe)..." -ForegroundColor DarkGray
Start-Process -FilePath $QQExe -WorkingDirectory (Split-Path $QQExe)
Start-Sleep -Seconds 3

# ---------- inject NapCat ----------
$NapCatDir = Join-Path $NapCatLocal "napcat"
$env:NAPCAT_PATCH_PACKAGE = Join-Path $NapCatDir "qqnt.json"
$env:NAPCAT_LOAD_PATH = Join-Path $NapCatDir "loadNapCat.js"
$env:NAPCAT_INJECT_PATH = Join-Path $NapCatDir "NapCatWinBootHook.dll"
$env:NAPCAT_LAUNCHER_PATH = Join-Path $NapCatDir "NapCatWinBootMain.exe"
$env:NAPCAT_MAIN_PATH = Join-Path $NapCatDir "napcat.mjs"

$mjsFwd = $env:NAPCAT_MAIN_PATH -replace '\\', '/'
"(async () => {await import('file:///$mjsFwd')})()" | Out-File -FilePath $env:NAPCAT_LOAD_PATH -Encoding UTF8

Write-Host "[..] Injecting NapCat..." -ForegroundColor Yellow
Start-Process -FilePath $env:NAPCAT_LAUNCHER_PATH `
    -ArgumentList $QQExe, $env:NAPCAT_INJECT_PATH `
    -WorkingDirectory $NapCatDir

# Wait for WebSocket
Write-Host "[..] Waiting for NapCat WebSocket (port 3001)..." -ForegroundColor Yellow
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 2
    if (netstat -ano 2>$null | Select-String "LISTENING.*3001") {
        Write-Host "[OK] NapCat ready! ws://127.0.0.1:3001" -ForegroundColor Green
        break
    }
}
Write-Host "WebUI: http://127.0.0.1:6099" -ForegroundColor DarkGray
