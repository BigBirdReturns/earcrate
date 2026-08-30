@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

set "REPO=%CD%"
set "PYTHON=%REPO%\.venv\Scripts\python.exe"
set "RUNNER=%REPO%\scripts\earcrate_stem_provider_repair.py"

if not exist "%PYTHON%" (
  echo [EarCrate] Refusing: expected live interpreter is missing:
  echo   %PYTHON%
  exit /b 1
)
if not exist "%RUNNER%" (
  echo [EarCrate] Refusing: provider repair runner is missing:
  echo   %RUNNER%
  exit /b 1
)

"%PYTHON%" "%RUNNER%" --apply --repo "%REPO%" --python "%PYTHON%"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo [EarCrate] Stem-provider activation refused. Read REFUSAL.json in the new Downloads receipt folder.
  exit /b %RC%
)

echo.
echo [EarCrate] Stem-provider proof passed. RESULT.json authorizes one unchanged Robi V3.1 run.
exit /b 0
