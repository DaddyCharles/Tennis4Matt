@echo off
title Ivan - Step 1: Setup

echo.
echo  ============================================================
echo    STEP 1 of 2  -  Setting up Ivan
echo  ============================================================
echo.
echo  This installs everything Ivan needs.
echo  It can take a few minutes - please leave this window open.
echo.

REM --- Run the main setup script in the app folder (one level up) ---
cd /d "%~dp0.."
call setup.bat

echo.
echo  ============================================================
echo    Ivan is ready! Double-click "Ivan" on your Desktop to start.
echo    (Or double-click 2_START.bat in this folder.)
echo  ============================================================
echo.
pause
