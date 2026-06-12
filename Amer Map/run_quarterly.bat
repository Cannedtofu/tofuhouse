@echo off
REM Quarterly store-locator scrape — triggered by Windows Task Scheduler
REM Runs at end of each quarter: Mar 31 / Jun 30 / Sep 30 / Dec 31

set SCRIPT_DIR=D:\代码项目\Amer Map
set PYTHON="D:\Visual Studio\Python39_64\python.exe"
set LOG=%SCRIPT_DIR%\logs\run_%date:~0,4%%date:~5,2%%date:~8,2%.log

REM Create logs folder if it doesn't exist
if not exist "%SCRIPT_DIR%\logs" mkdir "%SCRIPT_DIR%\logs"

echo [%date% %time%] Starting quarterly scrape >> "%LOG%"
cd /d "%SCRIPT_DIR%"
%PYTHON% main.py >> "%LOG%" 2>&1
echo [%date% %time%] Finished with exit code %ERRORLEVEL% >> "%LOG%"
