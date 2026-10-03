@echo off
chcp 65001 >nul
rem Збирання XprinterLabelStudio.exe на Windows (потрібен Python 3.10+ з python.org).
rem Якщо поруч є папка xprinter_driver з Xprinter.inf, вона вбудовується в exe.
python -m pip install --upgrade pyinstaller pillow qrcode python-barcode
python -m PyInstaller --noconfirm XprinterLabelStudio.spec
echo.
echo Готово: dist\XprinterLabelStudio.exe
pause
