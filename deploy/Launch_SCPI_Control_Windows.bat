@echo off
REM ============================================================================
REM  SCPI_Control launcher for WINDOWS lab PCs -- double-click me.
REM
REM  Lives on the ShareDrive next to launch_gui.sh (the Linux launcher).
REM  Design mirrors the Linux local-cache launcher:
REM    1. Mirror the app from the share to %LOCALAPPDATA%\SCPI_Control\app,
REM       but only when the version stamp changed, so later starts are quick.
REM    2. First run only: build a local Python venv and install the
REM       requirements. The share's pylibs are LINUX binaries, unusable here.
REM    3. Launch with the working directory ON THE SHARE so presets and bench
REM       profiles stay shared with the Linux bench PC.
REM
REM  On Windows the instrument tabs are view/edit only -- the app says so.
REM  Battery Data and Webcam are fully functional.
REM
REM  Every step prints what it is doing and every failure explains itself and
REM  waits, so nothing ever closes silently.
REM ============================================================================
setlocal EnableExtensions EnableDelayedExpansion
title SCPI Control - launcher

set "SHARE=%~dp0"
set "APPSRC=%SHARE%SCPI_Control"
set "CACHE=%LOCALAPPDATA%\SCPI_Control"
set "APP=%CACHE%\app"
set "VENV=%CACHE%\venv"
set "LOG=%CACHE%\setup.log"
set "WHY="
set "HOW="

echo ==============================================================
echo    SCPI Control  --  Windows launcher
echo ==============================================================
echo    Share : %APPSRC%
echo    Local : %CACHE%
echo.

if not exist "%CACHE%" mkdir "%CACHE%" >nul 2>&1

REM ---- 1/5 the share ---------------------------------------------------
echo [1/5] Checking the shared drive...
if not exist "%APPSRC%\gui.py" (
    set "WHY=The application was not found on the share - no gui.py under %APPSRC%"
    set "HOW=Check that the ShareDrive is connected in File Explorer and that this launcher is still inside the _software folder. If the folder is there but empty, run update_software.sh once on the Linux bench PC."
    goto :fail
)
echo       OK - application found on the share.
echo.

REM ---- 2/5 python ------------------------------------------------------
echo [2/5] Looking for Python 3...
set "PY="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    python3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=python3"
)
if not defined PY (
    set "WHY=No working Python 3 was found on this PC."
    set "HOW=Install Python 3.10 or newer from https://www.python.org/downloads/ and TICK the box 'Add python.exe to PATH' during setup. Then double-click this launcher again."
    goto :fail
)
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    set "WHY=The Python installed on this PC is older than 3.10."
    set "HOW=Install a current Python from https://www.python.org/downloads/ - tick 'Add python.exe to PATH' - then run this launcher again."
    goto :fail
)
%PY% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    set "WHY=This Python was installed without tkinter, the graphical toolkit the app needs."
    set "HOW=Re-run the Python installer, choose Modify, and enable the option 'tcl/tk and IDLE'. Then run this launcher again."
    goto :fail
)
for /f "usebackq tokens=*" %%v in (`%PY% -c "import sys;print(sys.version.split()[0])"`) do set "PYVER=%%v"
echo       OK - Python !PYVER!.
echo.

REM ---- 3/5 local copy --------------------------------------------------
echo [3/5] Checking the local copy of the app...
set "NEED=1"
if exist "%APP%\version.py" (
    fc "%APPSRC%\version.py" "%APP%\version.py" >nul 2>&1
    if not errorlevel 1 set "NEED=0"
)
if "!NEED!"=="1" (
    echo       New version on the share - copying, please wait...
    robocopy "%APPSRC%" "%APP%" /MIR /XD presets __pycache__ .git demos /NFL /NDL /NJH /NJS /R:2 /W:2 >>"%LOG%" 2>&1
    REM robocopy exit codes 0-7 are success, 8 and above are real failures
    if errorlevel 8 (
        set "WHY=Copying the app from the share to this PC failed."
        set "HOW=Check the ShareDrive connection and that there is free disk space, then try again. Details are in %LOG%."
        goto :fail
    )
    echo       OK - local copy updated.
) else (
    echo       OK - local copy is already up to date.
)
echo.

REM ---- 4/5 python packages ---------------------------------------------
echo [4/5] Checking Python packages...
set "INSTALL=!NEED!"
if not exist "%VENV%\Scripts\python.exe" (
    echo       First run on this PC - creating the Python environment.
    echo       This takes a few minutes and needs an internet connection.
    %PY% -m venv "%VENV%" >>"%LOG%" 2>&1
    if errorlevel 1 (
        set "WHY=Could not create the Python environment in %VENV%."
        set "HOW=Make sure Python 3.10 or newer is properly installed, then delete the folder %CACHE% and run this launcher again. Details are in %LOG%."
        goto :fail
    )
    set "INSTALL=1"
)
if "!INSTALL!"=="1" (
    echo       Installing/updating packages - needs internet, please wait...
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel >>"%LOG%" 2>&1
    "%VENV%\Scripts\python.exe" -m pip install -r "%APP%\requirements.txt" >>"%LOG%" 2>&1
    if errorlevel 1 (
        set "WHY=Installing the Python packages failed."
        set "HOW=Usually this means no internet connection, or a package has no version for this Python. Check the log lines below. Fixes that normally work - connect to the internet and retry, or install Python 3.12 and delete the folder %CACHE% before retrying."
        goto :fail
    )
    echo       OK - packages installed.
) else (
    echo       OK - packages already installed.
)
echo.

REM ---- 5/5 launch ------------------------------------------------------
echo [5/5] Starting SCPI Control...
echo.
echo       Keep this window open while the program runs.
echo       Closing this window closes the program.
echo.
REM pushd - not cd - so a UNC path like \\server\share is handled properly
pushd "%APPSRC%"
if errorlevel 1 (
    set "WHY=Could not open the share folder %APPSRC% as the working directory."
    set "HOW=Reconnect the ShareDrive in File Explorer and run this launcher again."
    goto :fail
)
"%VENV%\Scripts\python.exe" "%APP%\gui.py" %*
set "RC=!ERRORLEVEL!"
popd
if not "!RC!"=="0" (
    set "WHY=SCPI Control stopped with error code !RC!. The lines printed above are the actual error."
    set "HOW=Copy the text above and send it to the maintainer. If it mentions a missing module, delete the folder %VENV% and run this launcher again to rebuild the environment."
    goto :fail
)
echo.
echo  SCPI Control closed normally.
timeout /t 3 >nul
endlocal
exit /b 0

REM ---- failure handler --------------------------------------------------
:fail
echo.
echo --------------------------------------------------------------
echo   PROBLEM
echo     !WHY!
echo.
echo   WHAT TO DO
echo     !HOW!
echo --------------------------------------------------------------
if exist "%LOG%" (
    echo.
    echo   Last lines of the setup log - full log: %LOG%
    echo.
    powershell -NoProfile -Command "Get-Content -Tail 20 -Path '%LOG%'" 2>nul
    if errorlevel 1 type "%LOG%"
)
echo.
pause
endlocal
exit /b 1
