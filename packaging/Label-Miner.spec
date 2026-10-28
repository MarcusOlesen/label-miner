# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller recipe: src/label_miner -> dist\Label-Miner\Label-Miner.exe
#
# Built by packaging\build.ps1, or on its own from the repository root:
#   python -m PyInstaller packaging\Label-Miner.spec --noconfirm
#
# "onedir" rather than "onefile": onefile would unpack the whole app into a
# temp folder on every launch - slower, and more often flagged by antivirus
# programs.

import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
ICON = os.path.join(SRC, "label_miner", "assets", "label_miner.ico")

# Packages that load data files or import modules dynamically, and have no
# pyinstaller-hooks-contrib hook to collect them. (onnxruntime, cv2, sv_ttk
# and tkinterdnd2 do have one.)
NEEDS_COLLECT_ALL = [
    "pillow_heif",
    "rapidocr",  # its OCR models, default configs and lazily imported engines
]

datas = []
binaries = []
hiddenimports = ["label_miner.ui.app"]

for package_name in NEEDS_COLLECT_ALL:
    package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

# util.resource_path() looks for this at the root of the bundle.
datas += [(ICON, ".")]

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Label-Miner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # set True temporarily to see tracebacks from the frozen app
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Label-Miner",
)
