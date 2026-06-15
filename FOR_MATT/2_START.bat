@echo off
title Ivan - Start
cd /d "%~dp0.."

if not exist "venv\Scripts\pythonw.exe" (
    echo.
    echo  [!] Ivan is not set up yet.
    echo      Please double-click 1_SETUP.bat first, then try again.
    echo.
    pause
    exit /b 1
)

REM --- Activate the private environment -----------------------------
call "venv\Scripts\activate.bat" >nul 2>&1

REM --- Launch Ivan silently in the background (no console window) ---
REM     The tray app opens your browser automatically after a moment
REM     and shows a green Ivan icon next to your clock.
start "" "venv\Scripts\pythonw.exe" run_tray.pyw

exit /b 0
