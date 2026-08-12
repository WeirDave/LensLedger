@echo off
title LensLedger
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

REM Install the small Python dependency set on first launch. Existing healthy
REM installations skip this step without contacting the network.
python -c "import PIL" >nul 2>&1
if errorlevel 1 (
    echo Preparing LensLedger for first use...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo LensLedger could not install its Python requirements.
        pause
        exit /b 1
    )
)

REM Remove the empty pre-v0.15 working-folder shell after older processes let go
REM of it. RD without /S is intentionally harmless if the directory is not empty.
if exist "%~dp0..\_PhotoIndex" rd "%~dp0..\_PhotoIndex" >nul 2>&1

REM A folder with neither the managed-install marker nor a .git checkout is a
REM raw extracted release ZIP that was started directly instead of through
REM Install LensLedger.cmd -- it will run fine, but "Check for updates" can
REM never do anything for it, since there is no managed copy to replace.
if not exist "%~dp0.lensledger-managed.json" if not exist "%~dp0.git" (
    echo.
    echo Note: this looks like a direct extracted copy of LensLedger, not an
    echo installed one. It will run fine, but it can't update itself. Run
    echo "Install LensLedger.cmd" once instead to enable automatic updates.
    echo.
)

REM Stop any previous LensLedger server on its dedicated local port so every
REM launch uses the current code, and close that old launcher's now-dead
REM window too (it would otherwise sit forever at the "pause" below) --
REM but only when it's unambiguously this same script, never an unrelated
REM window, which is why the check below matches on the parent process
REM being cmd.exe with this exact script on its command line.
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 5309 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $ownerId = $_.OwningProcess; $owner = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $ownerId) -ErrorAction SilentlyContinue; if ($owner) { $parent = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $owner.ParentProcessId) -ErrorAction SilentlyContinue; if ($parent -and $parent.Name -eq 'cmd.exe' -and $parent.CommandLine -like '*Start LensLedger.cmd*') { Stop-Process -Id $parent.ProcessId -Force -ErrorAction SilentlyContinue } }; Stop-Process -Id $ownerId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

python src\photo_search.py

if errorlevel 1 (
    echo.
    echo LensLedger could not start. Review the error above.
) else (
    echo.
    echo LensLedger has closed normally.
)

pause
