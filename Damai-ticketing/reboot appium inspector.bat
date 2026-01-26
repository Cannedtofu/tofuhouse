@echo off
echo ==========================
echo MuMu + Appium FULL RESET
echo ==========================

:: 1️⃣ Ensure MuMu adb is first in PATH
set PATH=Z:\MuMu模拟器\nx_device\12.0\shell;%PATH%
echo [DEBUG] PATH set to MuMu adb

:: 2️⃣ Kill old adb server
echo [DEBUG] Killing old adb server...
adb kill-server

:: 3️⃣ Start adb server
echo [DEBUG] Starting adb server...
adb start-server

:: 4️⃣ Check connected devices
echo [DEBUG] Checking connected devices...
adb devices
echo ==========================

:: 5️⃣ Kill and clear UiAutomator2 instrumentation
echo [DEBUG] Stopping UiAutomator2 server...
adb shell am force-stop io.appium.uiautomator2.server
adb shell am force-stop io.appium.uiautomator2.server.test

echo [DEBUG] Clearing UiAutomator2 app data...
adb shell pm clear io.appium.uiautomator2.server
adb shell pm clear io.appium.uiautomator2.server.test
echo ==========================

:: 6️⃣ Prompt to restart emulator if no device detected
for /f "skip=1 tokens=1" %%d in ('adb devices') do set device=%%d
if "%device%"=="" (
    echo [WARNING] No MuMu emulator detected.
    echo Please start/restart the MuMu emulator now.
    pause
)

:: 7️⃣ Final instructions
echo [INFO] Now start Appium server (GUI or CLI) and create a new Inspector session.
echo [INFO] Refresh source after session starts to see full XML.
pause
echo [DONE] Reset complete.
