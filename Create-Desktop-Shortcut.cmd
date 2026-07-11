@echo off
REM Drops a clean "EarCrate" icon on your Desktop that launches the app, and
REM removes any stale "Jukebreaker" shortcut left over from the old name.
setlocal
set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo [EarCrate] Heads up: Python was not found on PATH. The shortcut will be
  echo created, but the app needs Python 3 ^(https://www.python.org/downloads/,
  echo "Add Python to PATH"^) to actually launch.
  echo.
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $d=[Environment]::GetFolderPath('Desktop'); Get-ChildItem -LiteralPath $d -Filter 'Jukebreaker*.lnk' -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue; $l=$ws.CreateShortcut((Join-Path $d 'EarCrate.lnk')); $l.TargetPath=(Join-Path '%REPO%' 'Launch-EarCrate.cmd'); $l.WorkingDirectory='%REPO%'; $l.IconLocation='shell32.dll,138'; $l.Description='Launch EarCrate (local server + web app)'; $l.Save(); Write-Host 'Created EarCrate shortcut on your Desktop; removed any old Jukebreaker shortcut.'"

echo.
echo [EarCrate] Done. Double-click the "EarCrate" icon on your Desktop to launch.
pause
