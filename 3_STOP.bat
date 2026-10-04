@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto :nvenv

echo ============================================================
echo   STOP EVERYTHING
echo ============================================================
echo.
echo   Stops the collector and the dashboard that
echo   1_START_COLLECTING.bat started, using the saved pids.
echo.
".venv\Scripts\python.exe" tools\start_scanner.py --stop
echo.
echo   If something still holds the port, close any leftover
echo   command windows running main.py, then run 4_STATUS.bat
echo   to check.
echo.
pause
exit /b 0

:nvenv
echo.
echo   NO .venv FOUND. Run these once, in this folder:
echo.
echo       python -m venv .venv
echo       .venv\Scripts\pip install -r requirements-lock.txt
echo.
pause
exit /b 1