@echo off
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)
"D:\Visual Studio\Python39_64\python.exe" main.py
