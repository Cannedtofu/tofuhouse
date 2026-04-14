@echo off
chcp 65001 > nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
:: Overwrite the log file for this new session
echo =================================================== > scheduler_last_run.log
echo Starting scheduled task at %date% %time% >> scheduler_last_run.log
echo =================================================== >> scheduler_last_run.log

:: Call the main logic and append all stdout/stderr to the log
call :main >> scheduler_last_run.log 2>&1
exit /b %ERRORLEVEL%

:main
setlocal enabledelayedexpansion

set "TARGET_FILE=output\sku_lean_tracking.xlsx"
set "MAX_TRIES=5"
set "TRIES=0"

:loop
set /a TRIES+=1

:: Get modification time before running
set "TIME_BEFORE="
if exist "%TARGET_FILE%" (
    for %%F in ("%TARGET_FILE%") do set "TIME_BEFORE=%%~tF"
)

echo.
echo ===================================================
echo [Attempt !TRIES! of %MAX_TRIES%] Starting script...
echo ===================================================
.venv\Scripts\python.exe main.py

:: Get modification time after running
set "TIME_AFTER="
if exist "%TARGET_FILE%" (
    for %%F in ("%TARGET_FILE%") do set "TIME_AFTER=%%~tF"
)

:: Compare modification times
if not "!TIME_BEFORE!"=="!TIME_AFTER!" (
    echo [Success] %TARGET_FILE% was updated.
    exit /b 0
)

echo [Warning] %TARGET_FILE% was not updated!
if !TRIES! lss %MAX_TRIES% (
    echo Waiting 10 seconds before retrying...
    timeout /t 10 /nobreak >nul
    goto loop
)

echo [Error] Failed to update %TARGET_FILE% after %MAX_TRIES% attempts. Giving up.
exit /b 1
