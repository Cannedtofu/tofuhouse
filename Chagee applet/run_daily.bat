@echo off
:: Navigate to project directory
cd /d "d:\代码项目\Chagee applet"

:: Activate virtual environment
call .venv\Scripts\activate

:: Run automation script
python automate_and_email.py

:: Use timeout if you want to see the terminal for a few seconds before closing
:: timeout /t 10
