@echo off
cd /d "%~dp0.."

:: ============================================================
::  dnd-dm-agent quick start
::  Launches NapCat QQ + nanobot gateway
::  Double-click to start everything.
:: ============================================================

title dnd-dm-agent (Quick)

echo ============================================
echo   dnd-dm-agent (Quick Start)
echo ============================================

:: ---------- NapCat QQ ----------
if not exist "localqq\NapCat.44498.Shell\QQ.exe" (
    echo [!] QQ.exe not found
    echo     Run: powershell -ExecutionPolicy Bypass -File scripts\setup-napcat.ps1
    goto gateway
)

echo [..] Starting NapCat QQ (saved login)...
start "NapCat-QQ" /min powershell -NoProfile -ExecutionPolicy Bypass -File "localqq\start-quick.ps1"

:: ---------- nanobot gateway ----------
:gateway
echo [..] Starting nanobot gateway (GPU mode)...
start "nanobot-gateway" /min cmd /c "cd /d %cd% && set DND_EMBEDDING_DEVICE=cuda && nanobot gateway"

:: ---------- done ----------
echo.
echo ============================================
echo   Starting up...
echo   WebUI:  http://127.0.0.1:18765
echo   NapCat: http://127.0.0.1:6099 (ws://127.0.0.1:3001)
echo ============================================
echo.
echo   Close this window after both are ready.
pause
