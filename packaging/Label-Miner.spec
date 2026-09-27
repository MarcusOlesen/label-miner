# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller recipe: src/label_miner -> dist\Label-Miner\Label-Miner.exe
#
# Built by packaging\build.ps1, or on its own from the repository root:
#   python -m PyInstaller packaging\Label-Miner.spec --noconfirm
# The Docling models must be fetched first (packaging\fetch_models.ps1).
#
# "onedir" rather than "onefile": onefile would unpack about 1.5 GB of
# torch/docling into a temp folder on every launch - slow, and more often
# flagged by antivirus programs.

import os

from PyInstaller.utils.hooks import collect_all, get_package_paths

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
ICON = os.path.join(SRC, "label_miner", "assets", "label_miner.ico")

# Packages that load data files or import modules dynamically, and have no
# pyinstaller-hooks-contrib hook to collect them. (torch, torchvision,
# transformers, cv2, sv_ttk and tkinterdnd2 do have one.)
NEEDS_COLLECT_ALL = [
    "docling",
    "docling_core",
    "docling_ibm_models",
    "docling_parse",
    "huggingface_hub",
    "pillow_heif",
    "rapidocr",  # its character dictionaries and default configs
]

datas = []
binaries = []
hiddenimports = ["label_miner.ui.app"]

for package_name in NEEDS_COLLECT_ALL:
    package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

# torchvision 0.29 names its compiled ops "_C_stable.pyd" / "image_stable.pyd",
# but the contrib hook still looks for "_C" / "image". They aren't importable
# modules either (torch loads them as plain DLLs), so nothing picks them up
# automatically - and the frozen app then fails at runtime with "operator
# torchvision::nms does not exist". Copy them in by hand.
torchvision_dir = get_package_paths("torchvision")[1]
for library in ("_C_stable.pyd", "image_stable.pyd"):
    path = os.path.join(torchvision_dir, library)
    if not os.path.isfile(path):
        raise RuntimeError(
            f"Expected torchvision file not found: {path}\n"
            "torchvision may have renamed its compiled extensions again - "
            "check site-packages\\torchvision\\ and update this list."
        )
    binaries.append((path, "torchvision"))

# util.resource_path() looks for these at the root of the bundle.
datas += [
    (os.path.join(ROOT, "docling-models"), "docling-models"),
    (ICON, "."),
]

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
