@echo off
title Tablet Mirror - Setup and Run

echo ============================================================
echo   Tablet Screen Mirror Solution
echo ============================================================
echo.

:: Check Python
python --version > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo         Install from https://www.python.org/downloads/
    pause & exit /b 1
)

:: Check ADB
adb version > nul 2>&1
if errorlevel 1 (
    echo [WARNING] adb not found in PATH.
    echo           Install Android SDK Platform-Tools and add to PATH.
    echo           https://developer.android.com/tools/releases/platform-tools
    echo.
    echo Press any key to continue anyway...
    pause > nul
)

:: Install pip packages
echo [1/3] Installing Python packages...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet flask flask-socketio pillow openpyxl pypdf Appium-Python-Client selenium easyocr numpy opencv-python-headless torch

if errorlevel 1 (
    echo [ERROR] Package install failed. Check your internet connection.
    pause & exit /b 1
)
echo        Done.
echo.

if not defined OPENAI_API_KEY (
    echo [INFO] OPENAI_API_KEY is not set. AI TC RUN will use standard fallback.
    echo.
)
echo [INFO] AI Random Run defaults to Codex CLI. Use codex login with ChatGPT.
echo [INFO] RANDOM_AI_PROVIDER=openai_api selects the separate paid API instead.
echo.

:: Show connected devices
echo [2/3] Checking ADB devices...
adb start-server > nul 2>&1
adb devices
echo.

:: Menu
echo [3/3] Select mode:
echo.
echo   [1] Screen Mirror only      (mirror_server.py)
echo   [2] Appium automation demo  (appium_controller.py)
echo   [3] Exit
echo.
set /p CHOICE=Enter number (1/2/3):

if "%CHOICE%"=="1" goto :mirror
if "%CHOICE%"=="2" goto :appium
if "%CHOICE%"=="3" exit /b 0
echo Invalid input.
pause & exit /b 1

:mirror
echo.
echo ============================================================
echo   Starting mirror server...
echo   Open browser: http://localhost:5000
echo   Then click [Start Mirror] button.
echo   Press Ctrl+C to stop.
echo ============================================================
echo.
python mirror_server.py
pause
exit /b 0

:appium
echo.
echo ============================================================
echo   Appium server must be running in another terminal.
echo   Run: appium
echo ============================================================
echo.
echo Select demo:
echo   [1] basic   - Connection test + home screenshot
echo   [2] youtube - YouTube search automation
echo   [3] macro   - Scroll macro x10
echo.
set /p DEMO=Enter number (1/2/3):

if "%DEMO%"=="1" python appium_controller.py --demo basic
if "%DEMO%"=="2" python appium_controller.py --demo youtube
if "%DEMO%"=="3" python appium_controller.py --demo macro
pause
exit /b 0
