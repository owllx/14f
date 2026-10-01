@echo off
chcp 65001 >nul
rem Збирання ServoTrimSync.exe на Windows (потрібен Python 3.10+ з python.org).
python -m pip install --upgrade pyinstaller pymavlink pyserial
python -m PyInstaller --noconfirm --onefile --windowed --name ServoTrimSync --icon app.ico --hidden-import pymavlink.dialects.v20.ardupilotmega --hidden-import pymavlink.dialects.v10.ardupilotmega --exclude-module numpy --exclude-module PIL servo_sync.py
echo.
echo Готово: dist\ServoTrimSync.exe
pause
