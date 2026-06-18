@echo off
setlocal EnableExtensions
chcp 65001 >nul

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
for %%I in ("%ROOT%\..\napcat") do set "NAPCAT_HOME=%%~fI"
set "CALLBACK_PORT=%NAPCAT_CALLBACK_PORT%"
if "%CALLBACK_PORT%"=="" set "CALLBACK_PORT=8011"
set "NAPCAT_SOURCE=%NAPCAT_SOURCE_DIR%"
if "%NAPCAT_SOURCE%"=="" if exist "%NAPCAT_HOME%\pkg" set "NAPCAT_SOURCE=%NAPCAT_HOME%\pkg"
if "%NAPCAT_SOURCE%"=="" if exist "%NAPCAT_HOME%\runtime" set "NAPCAT_SOURCE=%NAPCAT_HOME%\runtime"
if "%NAPCAT_SOURCE%"=="" if exist "%ROOT%\tools\napcat\pkg" set "NAPCAT_SOURCE=%ROOT%\tools\napcat\pkg"
set "NAPCAT_LAUNCHER="

echo Checking DND DM callback on port %CALLBACK_PORT%...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $s=Invoke-RestMethod 'http://127.0.0.1:%CALLBACK_PORT%/integrations/status' -TimeoutSec 1 -ErrorAction Stop; if ($null -ne $s.parsing) { exit 0 } } catch {}; $connections=Get-NetTCPConnection -State Listen -LocalPort %CALLBACK_PORT% -ErrorAction SilentlyContinue; if ($connections) { exit 2 }; exit 1"
if errorlevel 2 (
  echo Port %CALLBACK_PORT% is occupied by another service.
  pause
  exit /b 1
)
if errorlevel 1 (
  start "DND DM NapCat Callback" /min cmd /k call "%~dp0run_napcat_callback.bat" %CALLBACK_PORT%
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; 1..30 | ForEach-Object { try { $s=Invoke-RestMethod 'http://127.0.0.1:%CALLBACK_PORT%/integrations/status' -TimeoutSec 1 -ErrorAction Stop; if ($null -ne $s.parsing) { $ok=$true; break } } catch {}; Start-Sleep -Milliseconds 500 }; if (-not $ok) { exit 1 }"
if errorlevel 1 (
  echo Callback service did not become ready on port %CALLBACK_PORT%.
  pause
  exit /b 1
)

if "%NAPCAT_SOURCE%"=="" (
  echo NapCat installed-QQ runtime was not found.
  echo Set NAPCAT_SOURCE_DIR or install it under "%NAPCAT_HOME%".
  pause
  exit /b 1
)

for /r "%NAPCAT_SOURCE%" %%F in (launcher-user.bat) do (
  if exist "%%~fF" (
    set "NAPCAT_LAUNCHER=%%~fF"
    goto launcher_found
  )
)

:launcher_found
if "%NAPCAT_LAUNCHER%"=="" (
  echo NapCat installed-QQ launcher was not found.
  echo Set NAPCAT_SOURCE_DIR to a directory containing launcher-user.bat.
  pause
  exit /b 1
)

echo Using NapCat launcher:
echo   %NAPCAT_LAUNCHER%
if /i "%~1"=="--check" exit /b 0

for %%F in ("%NAPCAT_LAUNCHER%") do cd /d "%%~dpF"
call "%NAPCAT_LAUNCHER%" %*
exit /b %errorlevel%
