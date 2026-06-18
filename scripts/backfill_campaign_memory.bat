@echo off
setlocal
set "CAMPAIGN_ID=%~1"
if "%CAMPAIGN_ID%"=="" set "CAMPAIGN_ID=campaign_001"
set "API_BASE=%~2"
if "%API_BASE%"=="" set "API_BASE=http://127.0.0.1:8011"

echo Backfilling campaign memory for %CAMPAIGN_ID%...
curl.exe -sS -X POST "%API_BASE%/campaigns/%CAMPAIGN_ID%/memories/backfill"
echo.
endlocal
