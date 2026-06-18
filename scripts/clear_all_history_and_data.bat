@echo off
setlocal EnableExtensions EnableDelayedExpansion
for %%I in ("%~dp0..") do set "ROOT=%%~fI"

echo ============================================================
echo  DND DM Agent - Clear ALL conversation and structured data
echo ============================================================
echo.
echo This will permanently delete:
echo   - All campaign/game tables in the application database
echo   - Campaigns, characters, lobby sessions, messages and memory
echo   - Tasks, bindings, drafts and settings
echo   - Exported character sheets, uploads and logs
echo.
echo It will KEEP the RAG indexes (rule_chunks and compendium_entries),
echo source rulebooks, templates, configuration, code and catalogs.
echo.
echo Stop the backend service before continuing.
echo.

set /p "CONFIRM=Continue? Type Y to delete everything, or N to cancel: "
if /I "%CONFIRM%"=="N" goto :cancel
if /I not "%CONFIRM%"=="Y" goto :invalid

pushd "%ROOT%\backend"
where uv >nul 2>nul
if not errorlevel 1 (
  uv run python "..\tools\clear_runtime_data.py" --yes
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "..\tools\clear_runtime_data.py" --yes
) else (
  echo ERROR: uv and backend\.venv\Scripts\python.exe were not found.
  popd
  pause
  exit /b 1
)
set "RESULT=!ERRORLEVEL!"
popd

if not "%RESULT%"=="0" (
  echo.
  echo Cleanup failed. Check the error above. The backend may still be running.
  pause
  exit /b %RESULT%
)

echo.
echo Cleanup completed. The database will be recreated on next startup.
pause
exit /b 0

:cancel
echo Cancelled. No data was deleted.
pause
exit /b 0

:invalid
echo Invalid input. Only Y or N is accepted. No data was deleted.
pause
exit /b 1
