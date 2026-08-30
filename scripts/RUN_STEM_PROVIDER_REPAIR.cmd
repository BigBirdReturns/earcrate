@echo off
setlocal
cd /d "%~dp0.."

set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" scripts\earcrate_stem_provider_repair.py --apply
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo [EarCrate] Real stem-provider proof passed. Review RESULT.json in Downloads.
  echo [EarCrate] The unchanged Robi V3.1 campaign is authorized for one fresh run.
) else (
  echo [EarCrate] Stem-provider repair refused. Review REFUSAL.json in Downloads.
  echo [EarCrate] Robi remains locked.
)
echo.
pause
exit /b %RC%
