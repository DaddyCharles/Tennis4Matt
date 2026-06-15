@echo off
cd /d "%~dp0"

REM --- Make sure setup has been run --------------------------------
if not exist "venv\Scripts\pythonw.exe" (
    echo.
    echo  It looks like setup has not finished yet.
    echo  Please run setup.bat first, then try again.
    echo.
    echo  Press any key to close this window...
    pause >nul
    exit /b 1
)

REM --- Launch the app silently in the background (no console) -------
start "" "venv\Scripts\pythonw.exe" run_tray.pyw
exit /b 0
