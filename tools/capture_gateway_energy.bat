@echo off
REM One-shot gateway energy capture (0x2AB2 / 0x2AB8 at 0x792->0x710)
REM Run from the repo root: C:\Users\kings\OneDrive\Documents\odb_scanner

cd /d "%~dp0.."
call .venv\Scripts\activate.bat 2>nul
python tools\capture_gateway_energy.py
pause