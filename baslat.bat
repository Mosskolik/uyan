@echo off
cd /d "%~dp0"
if not exist .venv (
    echo Ilk kurulum yapiliyor, biraz bekle...
    python -m venv .venv
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\pip install -r requirements.txt
)
.venv\Scripts\python uyan.py %*
pause
