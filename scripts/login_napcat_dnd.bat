@echo off
setlocal EnableExtensions
chcp 65001 >nul

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
for %%I in ("%ROOT%\..\napcat") do set "NAPCAT_HOME=%%~fI"
set "NAPCAT_SOURCE=%NAPCAT_SOURCE_DIR%"
if "%NAPCAT_SOURCE%"=="" if exist "%NAPCAT_HOME%\pkg" set "NAPCAT_SOURCE=%NAPCAT_HOME%\pkg"
if "%NAPCAT_SOURCE%"=="" if exist "%NAPCAT_HOME%\runtime" set "NAPCAT_SOURCE=%NAPCAT_HOME%\runtime"
if "%NAPCAT_SOURCE%"=="" if exist "%ROOT%\tools\napcat\runtime" set "NAPCAT_SOURCE=%ROOT%\tools\napcat\runtime"
if "%NAPCAT_SOURCE%"=="" if exist "%ROOT%\tools\napcat\pkg" set "NAPCAT_SOURCE=%ROOT%\tools\napcat\pkg"
set "CALLBACK_PORT=%NAPCAT_CALLBACK_PORT%"
if "%CALLBACK_PORT%"=="" set "CALLBACK_PORT=8011"

echo [1/3] Checking DND DM callback service...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ours=$false; try { $s=Invoke-RestMethod 'http://127.0.0.1:%CALLBACK_PORT%/integrations/status' -TimeoutSec 1 -ErrorAction Stop; $ours=($null -ne $s.parsing) } catch {}; if (-not $ours) { $connections=Get-NetTCPConnection -State Listen -LocalPort %CALLBACK_PORT% -ErrorAction SilentlyContinue; foreach ($connection in $connections) { $process=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $connection.OwningProcess); Write-Host ('Port %CALLBACK_PORT% is occupied by: ' + $process.CommandLine); exit 2 } }"
if errorlevel 2 (
  echo Cannot replace the unknown service using port %CALLBACK_PORT%.
  pause
  exit /b 1
)

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%CALLBACK_PORT% " ^| findstr "LISTENING"') do set "CALLBACK_PID=%%P"
if defined CALLBACK_PID (
  echo Callback port %CALLBACK_PORT% is already listening.
) else (
  start "DND DM NapCat Callback" /min cmd /k call "%~dp0run_napcat_callback.bat" %CALLBACK_PORT%
)

echo Waiting for callback health check and initializing the demo campaign...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; 1..20 | ForEach-Object { try { $s=Invoke-RestMethod 'http://127.0.0.1:%CALLBACK_PORT%/integrations/status' -TimeoutSec 1 -ErrorAction Stop; if ($null -ne $s.parsing) { $ok=$true; break } } catch {}; Start-Sleep -Milliseconds 500 }; if (-not $ok) { exit 1 }; Invoke-RestMethod -Method Post 'http://127.0.0.1:%CALLBACK_PORT%/demo/bootstrap' -ErrorAction Stop | Out-Null"
if errorlevel 1 (
  echo Callback service did not become ready. Check the callback window for errors.
  pause
  exit /b 1
)

echo [2/3] Checking NapCat launcher...
if "%NAPCAT_SOURCE%"=="" (
  echo NapCat runtime not found. Set NAPCAT_SOURCE_DIR or install it under:
  echo   %NAPCAT_HOME%\pkg
  pause
  exit /b 1
)
set "NAPCAT_LAUNCHER="
for /r "%NAPCAT_SOURCE%" %%F in (launcher-user.bat) do (
  if exist "%%~fF" (
    set "NAPCAT_LAUNCHER=%%~fF"
    goto launcher_found
  )
)
:launcher_found
if "%NAPCAT_LAUNCHER%"=="" (
  echo launcher-user.bat was not found under "%NAPCAT_SOURCE%".
  pause
  exit /b 1
)

echo [3/3] Starting NapCat QQ login window...
echo After QQ opens, scan the QR code or complete the normal QQ login.
echo OneBot callback: http://127.0.0.1:%CALLBACK_PORT%/napcat/callback
if /i "%~1"=="--check" (
  echo Startup check passed. NapCat launcher:
  echo   %NAPCAT_LAUNCHER%
  exit /b 0
)
for %%F in ("%NAPCAT_LAUNCHER%") do cd /d "%%~dpF"
call "%NAPCAT_LAUNCHER%" %*

echo NapCat/QQ exited.
pause
