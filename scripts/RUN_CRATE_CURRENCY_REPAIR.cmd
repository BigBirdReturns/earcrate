@echo off
setlocal
set "REPO=%~dp0.."
cd /d "%REPO%"
set PYTHONUTF8=1

python scripts\earcrate_crate_currency_repair.py --apply --profile girl_talk_v1 --target-seconds 30 %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo EarCrate crate currency recovered. Read RESULT.json in the new Downloads receipt folder.
) else (
  echo EarCrate crate currency recovery did not authorize the Robi rerun. Read REFUSAL.json or RESULT.json in the new Downloads receipt folder.
)
exit /b %RC%
