@echo off
:: Navigate to project directory
cd /d "d:\代码项目\Chagee applet"

:: Setup log file
set LOG_FILE="@AutomationLog.txt"

echo ---------------------------------------- > %LOG_FILE%
echo [%DATE% %TIME%] Starting Daily Automation >> %LOG_FILE%

:: Activate virtual environment
call .venv\Scripts\activate >> %LOG_FILE% 2>&1

:: Run automation script (append all stdout and stderr to log file)
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
