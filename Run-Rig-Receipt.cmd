@echo off
REM Thin Windows wrapper for the EarCrate rig-receipt harness.
REM Forwards ALL arguments verbatim to scripts\run_rig_receipt.py and propagates
REM its exit code (0 complete / 1 failed / 2 incomplete). This wrapper contains no
REM logic of its own — everything lives in the Python script so behavior is
REM identical on every platform.
REM
REM Example:
REM   Run-Rig-Receipt.cmd ^
REM     --workspace "D:\EarCrate" ^
REM     --scratch "D:\EarCrate-Rig-Receipt\2026-07-16" ^
REM     --profile remix_prettylights_v1 ^
REM     --real-seconds 120 ^
REM     --piano-iterations 3
setlocal
set "HERE=%~dp0"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%HERE%scripts\run_rig_receipt.py" %*
) else (
  python "%HERE%scripts\run_rig_receipt.py" %*
)
exit /b %ERRORLEVEL%
