@echo off
REM Windows'ta UYAN.exe olusturur (dist\UYAN.exe)
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --onefile --name UYAN --collect-all mediapipe --collect-data cv2 uyan.py
echo.
echo Hazir: dist\UYAN.exe
pause
