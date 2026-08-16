@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_GENERATIVE_FLOOR.ps1" %*
exit /b %ERRORLEVEL%
