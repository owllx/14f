@echo off
rem Збирання XprinterLabelStudio.exe на Windows (потрібен Python 3.10+ з python.org).
rem Якщо поруч є папка xprinter_driver з Xprinter.inf, вона вбудовується в exe.
python -m pip install --upgrade pyinstaller pillow qrcode python-barcode
set EXTRA=
if exist xprinter_driver\Xprinter.inf set EXTRA=--add-data "xprinter_driver;xprinter_driver"
python -m PyInstaller --noconfirm --onefile --windowed --name XprinterLabelStudio --icon app.ico ^
  --hidden-import barcode.writer --collect-submodules barcode --collect-data barcode ^
  %EXTRA% label_designer.py
echo.
echo Готово: dist\XprinterLabelStudio.exe
pause
