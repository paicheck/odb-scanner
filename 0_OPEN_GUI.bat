@echo off
setlocal
cd /d "%~dp0"

rem ===========================================================================
rem  Opens the desktop GUI -- gui\app.py, the window that reads the database
rem  and offers the same six actions as the shortcuts in this folder.
rem
rem  It launches through pythonw, not python. python.exe is a console program,
rem  so it would leave a black window sitting behind the GUI for as long as the
rem  GUI is open. pythonw.exe is the same interpreter with no console.
rem
rem  Unlike the other shortcuts this one does not REQUIRE a .venv. The GUI is
rem  standard-library Tkinter plus this project's own imports, so whatever
rem  python is on the PATH opens it too. A .venv is still preferred when one
rem  exists, because that is the interpreter the other shortcuts use.
rem
rem  There is no pause on the success path on purpose: that would defeat using
rem  pythonw, by leaving a console window open every time. An error still
rem  pauses, so the reason stays on screen.
rem ===========================================================================

set "PY=.venv\Scripts\pythonw.exe"
if exist "%PY%" goto :launch
set "PY=pythonw"
where pythonw >nul 2>nul
if errorlevel 1 goto :nopython

:launch
start "" "%PY%" gui\app.py
exit /b 0

:nopython
echo.
echo ============================================================
echo   NO PYTHON FOUND
echo ============================================================
echo.
echo   Looked for pythonw.exe in .venv\Scripts and on your PATH
echo   and found neither, so there is nothing to open the window.
echo.
echo   Install Python 3.11 or newer from python.org and tick
echo   "Add python.exe to PATH" during setup. Or create the
echo   project environment once, in this folder:
echo.
echo       python -m venv .venv
echo       .venv\Scripts\pip install -r requirements-lock.txt
echo.
pause
exit /b 1
