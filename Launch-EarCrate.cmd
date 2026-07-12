@echo off
REM EarCrate launcher — fast path. Installs deps once, rebuilds the single-file
REM (a few seconds, so a git pull is always reflected), starts the local server
REM and opens the web app in your browser.
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

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo.
  echo [EarCrate] FFmpeg was not found on your PATH.
  echo Install FFmpeg, make sure ffmpeg.exe and ffprobe.exe are on PATH,
  echo then run this launcher again.
  echo.
  pause
  exit /b 1
)

where ffprobe >nul 2>nul
if errorlevel 1 (
  echo [EarCrate] ffprobe was not found on PATH. Install the complete FFmpeg package.
  pause
  exit /b 1
)

if not exist ".deps_installed" (
  echo [EarCrate] First run: installing dependencies...
  python -m pip install -r requirements.txt || (echo [EarCrate] pip install failed. & pause & exit /b 1)
  type nul > ".deps_installed"
)

python build\make_singlefile.py || (echo [EarCrate] build failed. & pause & exit /b 1)
python dist\earcrate.py
