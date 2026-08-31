@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PY="
if exist "main\.venv\Scripts\python.exe" set "PY=main\.venv\Scripts\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
  where py >nul 2>nul
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY set "PY=python"

%PY% "scripts\dispatch_robi_forward_phrase_resident.py" --project-root "%CD%"
exit /b %ERRORLEVEL%
