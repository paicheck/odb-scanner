@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto :nvenv

echo ============================================================
echo   STEP 1 of 2 -- checking the adapter link
echo ============================================================
echo.
echo   The ignition must be ON and the adapter plugged in.
echo   This takes about a minute.
echo.
".venv\Scripts\python.exe" main.py doctor
if errorlevel 1 goto :doctorfail

echo.
echo ============================================================
echo   STEP 2 of 2 -- starting the collector and dashboard
echo ============================================================
echo.
".venv\Scripts\python.exe" tools\start_scanner.py
if errorlevel 1 goto :startfail

echo.
echo --------------------------------------------------------
echo   STARTED.
echo.
echo   On THIS pc the dashboard is at  http://127.0.0.1:8000
echo.
echo   On your TABLET open  http://PC-IP-ADDRESS:8000
echo   where PC-IP-ADDRESS is this pc's address on the wifi,
echo   for example  http://192.168.1.42:8000
echo.
echo   The two must be on the same wifi network.
echo.
echo   To stop everything again, run  3_STOP.bat
echo --------------------------------------------------------
echo.
pause
exit /b 0

:doctorfail
echo.
echo ============================================================
echo   THE DOCTOR FAILED -- the collector was NOT started.
echo ============================================================
echo.
echo   The verdict above names the layer that stopped answering.
echo   The usual causes, in order of likelihood:
echo.
echo     1. Ignition off, or the car has gone to sleep.
echo        Turn the ignition ON and leave it ON, then retry.
echo.
echo     2. adapter.port points at the INCOMING Bluetooth port.
echo        Windows creates two. Use the OUTGOING one, described
echo        as an outgoing Serial over Bluetooth link.
echo        Run  2_DOCTOR.bat --all-ports  to find it.
echo.
echo     3. The adapter is not fully seated in the OBD socket.
echo.
echo   Fix the cause, then run  1_START_COLLECTING.bat  again.
echo.
echo   You can still look at old data without collecting:
echo       1_START_COLLECTING.bat is not needed for that, just open
echo       the dashboard at http://127.0.0.1:8000
echo ============================================================
echo.
pause
exit /b 1

:startfail
echo.
echo   The doctor passed but the stack did not start cleanly.
echo   Read the message above, then check the logs in
echo   data\logs\  -- especially collector.log and web.log
echo.
pause
exit /b 1

:nvenv
echo.
echo ============================================================
echo   NO VIRTUAL ENVIRONMENT FOUND
echo ============================================================
echo.
echo   Expected .venv\Scripts\python.exe next to this file.
echo   To create it, open a command prompt here and run:
echo.
echo       python -m venv .venv
echo       .venv\Scripts\pip install -r requirements-lock.txt
echo.
echo   This is a one-time setup. After that these shortcuts work.
echo ============================================================
echo.
pause
exit /b 1