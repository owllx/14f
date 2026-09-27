# -*- mode: python ; coding: utf-8 -*-
# Збирання: python -m PyInstaller --noconfirm XprinterLabelStudio.spec
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("barcode")
if os.path.isfile(os.path.join("xprinter_driver", "Xprinter.inf")):
    datas.append(("xprinter_driver", "xprinter_driver"))
hiddenimports = ["barcode.writer"] + collect_submodules("barcode")

a = Analysis(["label_designer.py"], datas=datas, hiddenimports=hiddenimports)
# Відеокодек OpenCV (ffmpeg) для фото не потрібен — не пакуємо його (мінус ~26 МБ).
a.binaries = [entry for entry in a.binaries if "opencv_videoio_ffmpeg" not in entry[0].lower()]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="XprinterLabelStudio",
    console=False,
    upx=True,
    icon=["app.ico"],
)
