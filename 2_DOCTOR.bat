@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto :nvenv

echo ============================================================
echo   CONNECTION DOCTOR
echo ============================================================
echo.
echo   Works out which layer stopped answering -- wrong COM port,
echo   unpaired adapter, pinned protocol, or a sleeping car.
echo.
echo   The ignition must be ON. This takes about a minute.
echo.
echo   Leave this window open and read the VERDICT at the end.
echo.
echo   If it says the port is wrong, retry with:
echo       2_DOCTOR_ALL_PORTS.bat
echo.
echo   --------------------------------------------------------
echo.
".venv\Scripts\python.exe" main.py doctor
set RC=%ERRORLEVEL%
echo.
echo   --------------------------------------------------------
if "%RC%"=="0" (
    echo   RESULT: everything answered. You can run
    echo   1_START_COLLECTING.bat
) else (
    echo   RESULT: something did not answer. Read the verdict above.
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