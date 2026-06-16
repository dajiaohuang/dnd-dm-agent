@echo off
setlocal
set "ROOT=%~dp0"
set "UV=uv"
set "PORT=%~1"
if "%PORT%"=="" set "PORT=%NAPCAT_CALLBACK_PORT%"
if "%PORT%"=="" set "PORT=8011"
where %UV% >nul 2>nul
if errorlevel 1 (
  echo uv is not installed or not on PATH.
  exit /b 1
)
cd /d "%ROOT%backend"
if "%DATABASE_URL%"=="" set "DATABASE_URL=sqlite:///%ROOT:\=/%data/dm_agent.db"
set "DATA_DIR=%ROOT%data"
%UV% run --no-sync uvicorn app.main:app --host 127.0.0.1 --port %PORT%
