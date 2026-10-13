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

REM Install the small Python dependency set on first launch, and again whenever
REM a required package is missing -- an install made before a package joined
REM requirements.txt would otherwise never get it (HEIC photos need
REM pillow_heif). Existing healthy installations skip this step without
REM contacting the network.
python -c "import PIL, pillow_heif" >nul 2>&1
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

REM Exit code 75 means the server stopped to restart. This window stays open
REM and starts it again here, so a restart keeps the same terminal tab instead
REM of opening a new one (closing this window's process would close the tab).
REM An update may be installing meanwhile: the server leaves the marker file
REM below in place, the updater deletes it when it has finished, and this waits
REM for that for up to ten minutes. The loop is one parenthesised block, with no
REM labels or GOTOs, so cmd never re-reads this file after an update has
REM replaced it.
set "LENSLEDGER_RESTART_MARKER=%TEMP%\lensledger-restart-%RANDOM%%RANDOM%.pending"
if exist "%LENSLEDGER_RESTART_MARKER%" del "%LENSLEDGER_RESTART_MARKER%" >nul 2>&1

setlocal EnableDelayedExpansion
set "RESTART_FLAG="
for /l %%n in (1,1,100000) do (
    python src\photo_search.py !RESTART_FLAG! %*
    set "RC=!errorlevel!"
    if "!RC!"=="75" (
        echo Restarting LensLedger...
        for /l %%w in (1,1,600) do if exist "%LENSLEDGER_RESTART_MARKER%" ping -n 2 127.0.0.1 >nul
        del "%LENSLEDGER_RESTART_MARKER%" >nul 2>&1
        python -c "import PIL, pillow_heif" >nul 2>&1
        if errorlevel 1 python -m pip install -r requirements.txt
        cls
        set "RESTART_FLAG=--restarted"
    ) else (
        echo.
        if "!RC!"=="0" (
            echo LensLedger has closed normally.
        ) else (
            echo LensLedger could not start. Review the error above.
        )
        pause
        exit /b !RC!
    )
)
