@echo off
setlocal
title EarCrate setup
set "DEST=%USERPROFILE%\earcrate"
set "BRANCH=main"
set "NEEDRERUN=0"

echo.
echo  ============================================
echo   EarCrate one-click setup / updater
echo   installs to: %DEST%
echo  ============================================
echo.

rem ---- [1/5] find python (python.exe or the py launcher) ----
set "PYEXE="
where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  where py >nul 2>nul && set "PYEXE=py -3"
)
if not defined PYEXE (
  echo [1/5] Python not found - installing it now...
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  if errorlevel 1 goto :nowinget
  set "NEEDRERUN=1"
) else (
  echo [1/5] Python found.
)

rem ---- [2/5] ffmpeg ----
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [2/5] ffmpeg not found - installing it now...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  if errorlevel 1 goto :nowinget
  set "NEEDRERUN=1"
) else (
  echo [2/5] ffmpeg found.
)

if "%NEEDRERUN%"=="1" goto :rerun

rem ---- [3/5] download the latest source ----
echo [3/5] Downloading the latest EarCrate...
set "ZIP=%TEMP%\earcrate-src.zip"
curl -L -s -o "%ZIP%" "https://codeload.github.com/BigBirdReturns/earcrate/zip/refs/heads/%BRANCH%"
if errorlevel 1 goto :faildl
if exist "%TEMP%\earcrate-%BRANCH%" rmdir /s /q "%TEMP%\earcrate-%BRANCH%"
tar -xf "%ZIP%" -C "%TEMP%"
if errorlevel 1 goto :faildl
if not exist "%DEST%" mkdir "%DEST%"
xcopy /e /y /q "%TEMP%\earcrate-%BRANCH%\*" "%DEST%\" >nul

rem ---- [4/5] python packages + single-file build ----
echo [4/5] Installing Python packages (first time takes a few minutes)...
cd /d "%DEST%"
%PYEXE% -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :failpip
%PYEXE% build\make_singlefile.py
if errorlevel 1 goto :failbuild

rem ---- [5/5] desktop launcher ----
echo [5/5] Creating the desktop launcher...
(
  echo @echo off
  echo cd /d "%DEST%"
  echo %PYEXE% dist\earcrate.py
) > "%USERPROFILE%\Desktop\EarCrate.cmd"

echo.
echo  Done. Launching EarCrate now. Next time, just double-click
echo  the EarCrate icon on your Desktop. Re-run this installer any
echo  time to update to the latest version.
echo.
%PYEXE% dist\earcrate.py
goto :end

:rerun
echo.
echo  Installed the missing pieces. Please close this window and
echo  double-click get-earcrate.cmd ONE more time to finish.
echo.
pause
goto :end

:nowinget
echo.
echo  Could not auto-install. Install these two manually, then re-run:
echo    Python 3.12+  https://www.python.org/downloads/  (check "Add to PATH")
echo    ffmpeg        https://www.gyan.dev/ffmpeg/builds/
echo.
pause
goto :end

:faildl
echo.
echo  Download failed. Check your internet connection and re-run.
echo.
pause
goto :end

:failpip
echo.
echo  Package install failed. Re-run this file; if it keeps failing,
echo  run this by hand in this folder:  %PYEXE% -m pip install -r requirements.txt
echo.
pause
goto :end

:failbuild
echo.
echo  Build failed. Re-run this file to retry with a fresh download.
echo.
pause
goto :end

:end
endlocal
