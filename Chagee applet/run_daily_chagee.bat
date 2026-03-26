@echo off
:: Force UTF-8 encoding to prevent Chinese string corruption in Task Scheduler
chcp 65001 >nul
set PYTHONUTF8=1

:: Navigate strictly to the directory where this batch file is located
cd /d "%~dp0"

:: Setup log file
set LOG_FILE="@AutomationLog.txt"

echo ---------------------------------------- > %LOG_FILE%
echo [%DATE% %TIME%] Starting Daily Automation >> %LOG_FILE%

:: Run automation script using the global python path (Because your packages like 'cv2' are installed system-wide, not in .venv)
echo [%DATE% %TIME%] Running main.py (Reorganized)... >> %LOG_FILE%
python main.py >> %LOG_FILE% 2>&1

set STATUS=%ERRORLEVEL%
if %STATUS% equ 0 (
    echo [%DATE% %TIME%] Success: Process completed with exit code 0. >> %LOG_FILE%
) else (
    echo [%DATE% %TIME%] Error: Process failed with exit code %STATUS%. >> %LOG_FILE%
)

echo [%DATE% %TIME%] Daily Automation Finished. >> %LOG_FILE%
echo ---------------------------------------- >> %LOG_FILE%
echo. >> %LOG_FILE%

:: Optional UI-only timeout
:: timeout /t 10
