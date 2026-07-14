@echo off
REM ============================================================================
REM  EarCrate — DEBUG MODE
REM
REM  Same as Launch-EarCrate, but the backend writes a live logfile capturing
REM  every request and a FULL traceback for anything that fails. This opens a
REM  second "Backend Monitor" window that follows that log in real time, so you
REM  can watch what the backend is doing (and exactly where it breaks) while you
REM  click around in the app.
REM
REM  When something borks: copy the ERROR block from the monitor window (the one
REM  with the traceback) and hand it over — it names the exact file and line.
REM ============================================================================
setlocal
cd /d "%~dp0"

REM One explicit logfile that BOTH the app and the monitor window agree on,
REM placed next to this launcher so it is easy to find and share.
set "EARCRATE_DEBUG=%~dp0earcrate_debug.log"

REM Start each debug session from a clean log.
if exist "%EARCRATE_DEBUG%" del "%EARCRATE_DEBUG%" >nul 2>nul

REM Open the live backend monitor in its own window. It waits for the log to
REM appear, then follows it (PowerShell's Get-Content -Wait == tail -f).
start "EarCrate Backend Monitor" powershell -NoProfile -ExecutionPolicy Bypass -Command "$log=$env:EARCRATE_DEBUG; Write-Host ('EarCrate backend monitor  -  '+$log) -ForegroundColor Cyan; Write-Host 'Waiting for the backend to start...' -ForegroundColor DarkGray; while(-not (Test-Path $log)){Start-Sleep -Milliseconds 300}; Get-Content -Path $log -Wait -Tail 50"

REM Launch the app itself (dependency checks, build, serve) in THIS window.
call "%~dp0Launch-EarCrate.cmd"

endlocal
