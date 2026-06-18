@echo off
setlocal EnableExtensions EnableDelayedExpansion
for %%I in ("%~dp0..") do set "ROOT=%%~fI"

echo ============================================================
echo  DND DM Agent - Rebuild rulebook RAG
echo ============================================================
echo.
echo This will replace the current rule_chunks and compendium index
echo using files under data\rules, data\compendium, and data\raw.
echo The operation may take several minutes while embeddings are built.
echo.
set /p "CONFIRM=Continue? Type Y to rebuild, or N to cancel: "
if /I "%CONFIRM%"=="N" goto :cancel
if /I not "%CONFIRM%"=="Y" goto :invalid

pushd "%ROOT%\backend"
set "DATA_DIR=%ROOT%\data"
if "%DATABASE_URL%"=="" set "DATABASE_URL=sqlite:///%ROOT:\=/%/data/dm_agent.db"

where uv >nul 2>nul
if not errorlevel 1 (
  uv run python "..\tools\rebuild_rag.py"
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "..\tools\rebuild_rag.py"
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
  echo RAG rebuild failed. Check the errors above.
  pause
  exit /b %RESULT%
)

echo.
echo RAG rebuild completed successfully.
pause
exit /b 0

:cancel
echo Cancelled. The existing RAG index was not changed.
pause
exit /b 0

:invalid
echo Invalid input. Only Y or N is accepted.
pause
exit /b 1
