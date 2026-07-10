@echo off
REM ============================================================================
REM  SCPI_Control launcher for WINDOWS lab PCs -- double-click me.
REM
REM  Lives on the ShareDrive next to launch_gui.sh (the Linux launcher).
REM  What it does, mirroring the Linux local-cache design:
REM    1. Mirrors the app from the share to %LOCALAPPDATA%\SCPI_Control\app
REM       (only when the version stamp changed -- fast after the first run).
REM    2. First run only: creates a local Python venv and pip-installs the
REM       requirements (needs Python 3.10+ installed and internet access).
REM       The share's pylibs are LINUX binaries and are not used on Windows.
REM    3. Launches the GUI with the working directory ON THE SHARE so
REM       presets/ and bench profiles stay shared with the Linux bench.
REM
REM  On Windows the instrument tabs are view/edit-only (the app says so);
REM  Battery Data and Webcam are fully functional.
REM ============================================================================
setlocal EnableDelayedExpansion
set "SHARE=%~dp0"
set "APPSRC=%SHARE%SCPI_Control"
set "CACHE=%LOCALAPPDATA%\SCPI_Control"
set "APP=%CACHE%\app"
set "VENV=%CACHE%\venv"

if not exist "%APPSRC%\gui.py" (
    echo ERROR: %APPSRC%\gui.py not found -- run update_software.sh on the
    echo Linux bench first, or check that this .bat sits in _software\.
    pause
    exit /b 1
)

REM ---- find Python 3 ---------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python 3 was not found on this PC.
    echo Install it from https://www.python.org/downloads/
    echo   -^> IMPORTANT: tick "Add python.exe to PATH" in the installer,
    echo then double-click this file again.
    pause
    exit /b 1
)

REM ---- mirror the app locally when the version stamp changed ------------
set NEED=1
if exist "%APP%\version.py" (
    fc "%APPSRC%\version.py" "%APP%\version.py" >nul 2>nul && set NEED=0
)
if !NEED!==1 (
    echo Updating local copy of SCPI_Control...
    robocopy "%APPSRC%" "%APP%" /MIR /XD presets __pycache__ .git demos ^
        /NFL /NDL /NJH /NJS >nul
)

REM ---- venv + dependency install ------------------------------------------
REM First run creates the venv; any version change re-runs pip so newly
REM added requirements arrive too.
set INSTALL=!NEED!
if not exist "%VENV%\Scripts\pythonw.exe" (
    echo First run on this PC: setting up Python environment...
    echo This takes a few minutes and needs internet access. Please wait.
    %PY% -m venv "%VENV%"
    if errorlevel 1 (
        echo ERROR: could not create a Python environment. Is Python 3.10+
        echo installed? Check with:  python --version
        pause
        exit /b 1
    )
    set INSTALL=1
)
if !INSTALL!==1 (
    "%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip setuptools wheel
    "%VENV%\Scripts\python.exe" -m pip install --quiet -r "%APP%\requirements.txt"
    if errorlevel 1 (
        echo ERROR: package install failed (no internet?). Delete
        echo %VENV% and run this again once online.
        pause
        exit /b 1
    )
)

REM ---- launch: cwd on the SHARE so presets stay shared with Linux --------
cd /d "%APPSRC%"
start "SCPI Control" "%VENV%\Scripts\pythonw.exe" "%APP%\gui.py"
exit /b 0
