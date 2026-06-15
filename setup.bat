@echo off
setlocal enabledelayedexpansion
title Ivan - Setup

echo.
echo  ============================================================
echo    Ivan - One-Time Setup
echo  ============================================================
echo.
echo  This will get everything ready. It can take a few minutes.
echo  Please leave this window open until you see "Setup complete!"
echo.

REM --- 1. Check that Python is installed -----------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo  [!] Python is not installed on this computer.
    echo.
    echo      We are opening the Python download page for you now.
    echo      1. Download and run the installer.
    echo      2. IMPORTANT: tick "Add Python to PATH" on the first screen.
    echo      3. After it finishes, run this setup.bat again.
    echo.
    start "" "https://www.python.org/downloads/"
    echo  Press any key to close this window...
    pause >nul
    exit /b 1
)

echo  [1/5] Python found.

REM --- 2. Create the virtual environment ----------------------------
if not exist "venv\Scripts\python.exe" (
    echo  [2/5] Creating a private environment for the app...
    python -m venv venv
    if errorlevel 1 (
        echo.
        echo  [!] Could not create the environment.
        echo      Please make sure Python installed correctly, then try again.
        echo.
        pause
        exit /b 1
    )
) else (
    echo  [2/5] Environment already exists - reusing it.
)

REM --- 3. Install the required packages ------------------------------
echo  [3/5] Installing required packages (this is the slow part)...
call "venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
call "venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  [!] Could not install the required packages.
    echo      Please check your internet connection and try again.
    echo.
    pause
    exit /b 1
)

REM --- 4. Install the browser the bot uses ---------------------------
echo  [4/5] Installing the browser the bot uses...
call "venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 (
    echo.
    echo  [!] Could not install the browser component.
    echo      Please check your internet connection and try again.
    echo.
    pause
    exit /b 1
)

REM --- 5. Generate the app (PWA) icons ------------------------------
echo  [5/5] Generating app icons...
call "venv\Scripts\python.exe" app\generate_icons.py
if errorlevel 1 (
    echo  [!] Could not generate app icons (non-critical). Continuing...
)

REM --- Create a desktop shortcut to run.bat --------------------------
echo  Creating a desktop shortcut...
set "SHORTCUT=%USERPROFILE%\Desktop\Ivan.lnk"
set "TARGET=%~dp0run_tray.bat"
set "WORKDIR=%~dp0"
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%SHORTCUT%');" ^
  "$s.TargetPath='%TARGET%';" ^
  "$s.WorkingDirectory='%WORKDIR%';" ^
  "$s.IconLocation='%SystemRoot%\System32\shell32.dll,13';" ^
  "$s.Description='Start Ivan';" ^
  "$s.Save()" >nul 2>&1

echo.
echo  ============================================================
echo    Setup complete!
echo  ============================================================
echo.
echo  A shortcut called "Ivan" is now on your Desktop.
echo  Double-click it any time to start the app.
echo.
echo  Press any key to close this window...
pause >nul
exit /b 0
