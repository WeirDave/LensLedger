@echo off
title Install LensLedger
cd /d "%~dp0"

REM Run on the newest Python installed, not whichever happens to come first on
REM PATH: an older one left ahead of it never gets the newer packages. The py
REM launcher knows every installed Python; a pre-release is never chosen, and
REM neither is anything below the minimum, which falls back to PATH's python.
set "PY=python"
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) and sys.version_info.releaselevel == 'final' else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)
%PY% -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo.
    echo LensLedger needs Python 3.11 or newer.
    echo Download it from https://www.python.org/downloads/ and select "Add Python to PATH".
    echo.
    pause
    exit /b 1
)
%PY% src\environment_check.py --python-only
if errorlevel 1 (
    echo.
    pause
    exit /b 1
)

echo.
echo Installing LensLedger into your private per-user Programs folder...
if "%~1"=="" (
    %PY% src\lensledger_updater.py install-current
) else (
    echo Migrating the existing LensLedger catalog from: %~1
    %PY% src\lensledger_updater.py install-current --legacy-root "%~1"
)
if errorlevel 1 (
    echo.
    echo LensLedger was not installed. Your existing copy and photo data were not changed.
    pause
    exit /b 1
)

echo.
echo LensLedger is installed and starting.
timeout /t 3 >nul
