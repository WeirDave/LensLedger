@echo off
title Install LensLedger
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo LensLedger needs Python 3.11 or newer.
    echo Download it from https://www.python.org/downloads/ and select "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

echo.
echo Installing LensLedger into your private per-user Programs folder...
if "%~1"=="" (
    python lensledger_updater.py install-current
) else (
    echo Migrating the existing LensLedger catalog from: %~1
    python lensledger_updater.py install-current --legacy-root "%~1"
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
