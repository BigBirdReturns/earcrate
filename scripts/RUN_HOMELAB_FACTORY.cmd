@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_HOMELAB_FACTORY.ps1" %*
exit /b %ERRORLEVEL%
