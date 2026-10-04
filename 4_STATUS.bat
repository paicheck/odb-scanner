@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto :nvenv

echo ============================================================
echo   WHAT IS RUNNING RIGHT NOW
echo ============================================================
echo.
".venv\Scripts\python.exe" tools\start_scanner.py --status
echo.
echo   --------------------------------------------------------
echo   If the collector is alive but no new data appears on the
echo   dashboard, run  2_DOCTOR.bat -- all-ports  and check the
echo   adapter link.
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