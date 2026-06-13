@echo off
setlocal
set "ROOT=%~dp0"
set "PORT=%~1"
if "%PORT%"=="" set "PORT=3000"

where npm >nul 2>nul
if errorlevel 1 (
  echo npm is not installed or not on PATH.
  pause
  exit /b 1
)

cd /d "%ROOT%frontend"
if not exist "node_modules" npm install
echo Starting latest DND DM WebUI on http://127.0.0.1:%PORT%
echo Same-origin /api proxy defaults to http://127.0.0.1:8010
npm run dev -- --hostname 127.0.0.1 --port %PORT%
