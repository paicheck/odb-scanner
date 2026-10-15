@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto :nvenv

echo ============================================================
echo   DOCTOR -- TRY EVERY COM PORT
echo ============================================================
echo.
echo   Use this when 2_DOCTOR.bat says the configured port is
echo   missing, or that nothing answered on it. Windows creates
echo   two Bluetooth COM ports and the outgoing one is usually
echo   not COM3.
echo.
echo   Stage 2 lists every port with its description. Pick the
echo   one that says OUTGOING, then set it in config.yaml under
echo   adapter.port, then run 1_START_COLLECTING.bat
echo.
echo   --------------------------------------------------------
echo.
".venv\Scripts\python.exe" main.py doctor --all-ports
set RC=%ERRORLEVEL%
echo.
echo   --------------------------------------------------------
if "%RC%"=="0" (
    echo   RESULT: a port answered. Note which one, put it in
    echo   config.yaml as adapter.port, then 1_START_COLLECTING.bat
) else (
    echo   RESULT: no port answered. That points at the adapter
    echo   or the car, not the software. Check the ignition is
    echo   ON, the adapter is paired in Windows, and it is fully
    echo   seated in the OBD socket.
)
echo.
pause
exit /b %RC%

:nvenv
echo.
echo   NO .venv FOUND. Run these once, in this folder:
echo.
echo       python -m venv .venv
echo       .venv\Scripts\pip install -r requirements-lock.txt
echo.
pause
exit /b 1