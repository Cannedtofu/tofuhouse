@echo off
set /p username="Enter Twitter Username: "
set /p password="Enter Twitter Password: "
echo Generating token...
cd tools
python create_session_curl.py %username% %password% --append ../sessions.jsonl
cd ..
echo Done! You can now start the Nitter instance.
pause
