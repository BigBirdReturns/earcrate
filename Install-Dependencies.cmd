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
REM EarCrate needs BOTH ffmpeg.exe and ffprobe.exe on PATH. Check each: a
REM partial install with ffmpeg but no ffprobe otherwise passes here and then
REM fails later inside the app's doctor/self-test checks.
set "_FFMISSING="
where ffmpeg >nul 2>nul
if errorlevel 1 set "_FFMISSING=ffmpeg"
where ffprobe >nul 2>nul
if errorlevel 1 (
  if defined _FFMISSING (set "_FFMISSING=ffmpeg + ffprobe") else (set "_FFMISSING=ffprobe")
)
if defined _FFMISSING (
  echo [EarCrate] Python packages installed, but FFmpeg is still required ^(missing: %_FFMISSING%^).
  echo Install FFmpeg and put ffmpeg.exe AND ffprobe.exe on PATH before launch.
) else (
  echo [EarCrate] Python and FFmpeg dependencies are ready.
)
echo.
echo [EarCrate] Verify your setup any time with:  python -m earcrate doctor
echo You can now run Launch-EarCrate.cmd (or START_HERE.cmd).
pause
