@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_ROBI_WHOA_BED_FIRST_V2.ps1" %*
exit /b %ERRORLEVEL%
