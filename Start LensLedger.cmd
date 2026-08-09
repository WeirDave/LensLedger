@echo off
title LensLedger
cd /d "%~dp0"

REM Remove the empty pre-v0.15 working-folder shell after older processes let go
REM of it. RD without /S is intentionally harmless if the directory is not empty.
if exist "%~dp0..\_PhotoIndex" rd "%~dp0..\_PhotoIndex" >nul 2>&1

REM Stop any previous LensLedger server on its dedicated local port so every
REM launch uses the current code. The replacement server opens/refocuses the
REM browser at the same address after it is ready.
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 5309 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

python photo_search.py

if errorlevel 1 (
    echo.
    echo LensLedger could not start. Review the error above.
) else (
    echo.
    echo LensLedger has closed normally.
)

pause
