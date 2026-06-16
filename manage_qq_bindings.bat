@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%backend"
if "%DATABASE_URL%"=="" set "DATABASE_URL=sqlite:///%ROOT:\=/%data/dm_agent.db"
uv run --no-sync python -m app.integrations.manage_qq_bindings %*
