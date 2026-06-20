@echo off
cd /d "%~dp0.."

:: ============================================================
::  dnd-dm-agent quick start
::  NapCat QQ (saved login) + nanobot gateway (GPU)
::  Double-click to run.
:: ============================================================

title dnd-dm-agent (Quick)

echo ============================================
echo   dnd-dm-agent (Quick Start)
echo   NapCat QQ + Gateway (GPU)
echo ============================================
echo.
echo   First time? Use start-all.bat to scan QR.
echo.

:: ---------- NapCat QQ ----------
if not exist "localqq\NapCat.44498.Shell\QQ.exe" (
    echo [!] QQ.exe not found
    echo     Run: powershell -File scripts\setup-napcat.ps1
    goto gateway
)

echo [..] Starting NapCat QQ (saved login)...
start "NapCat-QQ" /min powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\napcat-start.ps1" -NoQR

:: ---------- nanobot gateway ----------
:gateway
echo [..] Starting nanobot gateway (GPU mode)...
start "nanobot-gateway" /min cmd /c "cd /d %cd% && set DND_EMBEDDING_DEVICE=cuda && nanobot gateway"

:: ---------- done ----------
echo.
echo ============================================
echo   Starting up...
echo   WebUI:  http://127.0.0.1:18765
echo   NapCat: http://127.0.0.1:6099
echo ============================================
echo.
echo   Close this window after both are ready.
pause
