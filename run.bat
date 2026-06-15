@echo off
title Ivan
cd /d "%~dp0"

REM --- Make sure setup has been run ---------------------------------
if not exist "venv\Scripts\python.exe" (
    echo.
    echo  It looks like setup has not finished yet.
    echo  Please run setup.bat first, then try again.
    echo.
    echo  Press any key to close this window...
    pause >nul
    exit /b 1
)

echo.
echo  ============================================================
echo    Ivan is starting...
echo  ============================================================
echo.
echo  Your browser will open automatically in a few seconds.
echo  Keep this window open while you use the app.
echo  To stop the app, just close this window.
echo.

REM --- Open the dashboard in the browser after a short delay --------
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start "" http://127.0.0.1:9999"

REM --- Start the app -----------------------------------------------
call "venv\Scripts\python.exe" main.py

REM --- If we get here, the app stopped or crashed ------------------
echo.
echo  ============================================================
echo    Something went wrong - please contact support.
echo  ============================================================
echo.
echo  The app has stopped. The details above may help support.
echo.
echo  Press any key to close this window...
pause >nul
exit /b 1
