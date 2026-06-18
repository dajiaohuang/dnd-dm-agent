@echo off
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%\backend"
if "%DATABASE_URL%"=="" set "DATABASE_URL=sqlite:///%ROOT:\=/%/data/dm_agent.db"
uv run --no-sync python -m app.integrations.manage_qq_bindings %*
