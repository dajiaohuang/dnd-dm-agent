@echo off
cd /d "%~dp0.."

:: ============================================================
::  dnd-dm-agent full startup
::  Usage:
::    start-all.bat                  full start (shows QR)
::    start-all.bat /NoQR            skip QR display
::    start-all.bat /NoQQ            skip NapCat QQ entirely
::    start-all.bat /CpuOnly         CPU mode for embeddings
::    start-all.bat /RestartGateway  kill + restart gateway
:: ============================================================

set NOQQ=0
set NOQR=0
set CPUONLY=0
set RESTART=0

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="/NoQQ" set NOQQ=1
if /i "%~1"=="/NoQR" set NOQR=1
if /i "%~1"=="/CpuOnly" set CPUONLY=1
if /i "%~1"=="/RestartGateway" set RESTART=1
shift
goto parse_args
:args_done

title dnd-dm-agent

echo ============================================
echo   dnd-dm-agent
echo ============================================

:: ---------- NapCat QQ ----------
if %NOQQ%==1 (
    echo [--] Skipping NapCat QQ (/NoQQ)
    goto gateway
)

if not exist "localqq\NapCat.44498.Shell\QQ.exe" (
    echo [!] QQ.exe not found
    echo     Run: powershell -File scripts\setup-napcat.ps1
    echo     Or skip: start-all.bat /NoQQ
    goto gateway
)

if %NOQR%==1 (
    echo [..] Starting NapCat QQ (no QR)...
    start "NapCat-QQ" /min powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\napcat-start.ps1" -NoQR
) else (
    echo [..] Starting NapCat QQ (QR will auto-open)...
    start "NapCat-QQ" /min powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\napcat-start.ps1"
)

:: ---------- nanobot gateway ----------
:gateway
if %RESTART%==1 (
    echo [..] Stopping old gateway...
    taskkill /F /IM python.exe >nul 2>&1
    timeout /t 2 /nobreak >nul
)

if %CPUONLY%==1 (
    echo [..] Starting nanobot gateway (CPU mode)...
    start "nanobot-gateway" /min cmd /c "cd /d %cd% && set DND_EMBEDDING_DEVICE= && nanobot gateway"
) else (
    echo [..] Starting nanobot gateway (GPU mode)...
    start "nanobot-gateway" /min cmd /c "cd /d %cd% && set DND_EMBEDDING_DEVICE=cuda && nanobot gateway"
)

:: ---------- done ----------
echo.
echo ============================================
echo   Starting up...
echo   WebUI:  http://127.0.0.1:18765
if %NOQQ%==0 echo   NapCat: http://127.0.0.1:6099
echo ============================================
echo.
echo   Close this window after everything is ready.
pause
