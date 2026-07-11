@echo off
REM Installs EarCrate's Python dependencies. Referenced by the import-error
REM message; also runnable on its own if a launch failed on a missing package.
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo [EarCrate] Python was not found on your PATH.
  echo Install Python 3 from https://www.python.org/downloads/
  echo and tick "Add Python to PATH" during setup, then run this again.
  echo.
  pause
  exit /b 1
)

echo [EarCrate] Installing dependencies from requirements.txt ...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [EarCrate] pip install failed. Check your internet connection and Python version.
  pause
  exit /b 1
)
type nul > ".deps_installed"
echo.
echo [EarCrate] Dependencies installed. You can now run Launch-EarCrate.cmd (or START_HERE.cmd).
pause
