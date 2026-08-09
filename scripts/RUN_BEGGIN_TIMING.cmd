@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_BEGGIN_TIMING.ps1" %*
exit /b %ERRORLEVEL%
