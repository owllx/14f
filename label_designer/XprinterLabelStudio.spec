# -*- mode: python ; coding: utf-8 -*-
# Збирання: python -m PyInstaller --noconfirm XprinterLabelStudio.spec
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("barcode")
if os.path.isfile(os.path.join("xprinter_driver", "Xprinter.inf")):
    datas.append(("xprinter_driver", "xprinter_driver"))
hiddenimports = ["barcode.writer"] + collect_submodules("barcode")

# Обробка фото працює лише на Pillow — numpy/OpenCV не потрібні (exe ≈ 17 МБ замість ~60 МБ).
a = Analysis(
    ["label_designer.py"], datas=datas, hiddenimports=hiddenimports,
    excludes=["numpy", "cv2", "matplotlib", "scipy", "pandas"],
)
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
