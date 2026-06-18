@echo off
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
for %%I in ("%ROOT%\..\napcat") do set "NAPCAT_HOME=%%~fI"
set "ARCHIVE=%NAPCAT_HOME%\NapCat.Shell.Windows.OneKey.zip"
set "TARGET=%NAPCAT_HOME%\runtime"
if not exist "%ARCHIVE%" (
  echo Missing NapCat archive: "%ARCHIVE%"
  exit /b 1
)
if not exist "%TARGET%" mkdir "%TARGET%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%ARCHIVE%' -DestinationPath '%TARGET%' -Force"
if errorlevel 1 exit /b 1
echo NapCat installer extracted to "%TARGET%".
echo Run "%TARGET%\NapCatInstaller.exe" to install or update the local NapCat shell.
