@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto :nvenv

echo ============================================================
echo   TRY IT WITHOUT A CAR
echo ============================================================
echo.
echo   Runs the whole pipeline against the built-in simulator:
echo   simulator, collector, database, analysis, web dashboard
echo   and the read-only guard. No car and no Ollama needed.
echo.
echo   Takes about 20 seconds. Every line should say PASS.
echo.
".venv\Scripts\python.exe" tools\smoke_test.py
set RC=%ERRORLEVEL%
echo.
echo   --------------------------------------------------------
if "%RC%"=="0" (
    echo   RESULT: pipeline OK. Nothing is wrong with the software.
    echo   If the real car will not connect, it is a vehicle or
    echo   adapter problem, so use  2_DOCTOR.bat
) else (
    echo   RESULT: something failed. The lines above name what.
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