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
echo [%DATE% %TIME%] Running Chagee main.py... >> %LOG_FILE%
python main.py >> %LOG_FILE% 2>&1

set STATUS=%ERRORLEVEL%
if %STATUS% equ 0 (
    echo [%DATE% %TIME%] Success: Chagee process completed with exit code 0. >> %LOG_FILE%
) else (
    echo [%DATE% %TIME%] Error: Chagee process failed with exit code %STATUS%. >> %LOG_FILE%
)

:: Run Guming automation script once Chagee completes
echo [%DATE% %TIME%] Running Guming main.py... >> %LOG_FILE%
cd /d "d:\代码项目\Guming applet"
python main.py >> "d:\代码项目\Guming applet\@AutomationLog.txt" 2>&1

set GUMING_STATUS=%ERRORLEVEL%
cd /d "%~dp0"
if %GUMING_STATUS% equ 0 (
    echo [%DATE% %TIME%] Success: Guming process completed with exit code 0. >> %LOG_FILE%
) else (
    echo [%DATE% %TIME%] Error: Guming process failed with exit code %GUMING_STATUS%. >> %LOG_FILE%
)

echo [%DATE% %TIME%] Daily Automation Finished. >> %LOG_FILE%
echo ---------------------------------------- >> %LOG_FILE%
echo. >> %LOG_FILE%

:: Optional UI-only timeout
:: timeout /t 10
