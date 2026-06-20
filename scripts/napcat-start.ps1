# Start NapCat QQ (first time: shows QR, saves login state)
# Called by start-all.bat
param([switch]$NoQR)

$ErrorActionPreference = "Continue"
$Base = $PSScriptRoot
Set-Location (Split-Path $Base)

$LocalQQ = Join-Path (Get-Location) "localqq"
if (-not (Test-Path $LocalQQ)) {
    Write-Host "[!] localqq not found. Run setup-napcat.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host "=== NapCat QQ ===" -ForegroundColor Cyan

# ---------- QR helper ----------
function Show-QR {
    param([int]$TimeoutSec = 30)
    $cacheDir = Join-Path $LocalQQ "NapCat.44498.Shell\versions\9.9.26-44498\resources\app\napcat\cache"

    # Check if QR already exists
    $existing = Get-ChildItem $cacheDir -Filter "*.png" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Where-Object { $_.Length -gt 1024 } |
        Select-Object -First 1
    if ($existing) {
        Write-Host "[OK] QR found: $($existing.Name)" -ForegroundColor Green
        Start-Process $existing.FullName
        return
    }

    # Record existing files to detect new ones
    $before = @{}
    Get-ChildItem $cacheDir -Filter "*.png" -ErrorAction SilentlyContinue |
        ForEach-Object { $before[$_.Name] = $_.LastWriteTime }

    Write-Host "[..] Waiting for QR code..." -ForegroundColor Yellow
    for ($i = 0; $i -lt $TimeoutSec; $i++) {
        $files = Get-ChildItem $cacheDir -Filter "*.png" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
        $qr = $files | Where-Object {
            $_.Length -gt 1024 -and (
                -not $before.ContainsKey($_.Name) -or
                $_.LastWriteTime -gt $before[$_.Name]
            )
        } | Select-Object -First 1
        if ($qr) {
            Write-Host "[OK] QR: $($qr.Name)" -ForegroundColor Green
            Start-Process $qr.FullName
            return
        }
        Write-Host "." -NoNewline
        Start-Sleep -Seconds 1
    }
    Write-Host ""
    Write-Host "[!] No QR appeared. Open:" -ForegroundColor DarkYellow
    Write-Host "    $cacheDir" -ForegroundColor DarkGray
}

# ---------- main ----------
$QQExe = Join-Path $LocalQQ "NapCat.44498.Shell\QQ.exe"
if (-not (Test-Path $QQExe)) {
    Write-Host "[!] QQ.exe not found. Run setup-napcat.ps1 first." -ForegroundColor Red
    exit 1
}

$QQRunning = Get-Process -Name "QQ" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "*localqq*" }

if (-not $QQRunning) {
    Write-Host "[..] Starting QQ..." -ForegroundColor Yellow
    Start-Process -FilePath $QQExe -WorkingDirectory (Split-Path $QQExe)
    if (-not $NoQR) { Show-QR }
} else {
    Write-Host "[OK] QQ already running (PID: $($QQRunning.Id))" -ForegroundColor Green
}

# Inject NapCat
$NapCatDir = Join-Path $LocalQQ "NapCat.44498.Shell\versions\9.9.26-44498\resources\app\napcat"
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
