# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for TalesRunner Fish Monitor (GUI)
#
# Build steps:
#   venv\Scripts\pip install pyinstaller
#   venv\Scripts\pyinstaller talesrunner.spec --clean
#
# Output: dist\TalesRunnerMonitor\TalesRunnerMonitor.exe

import sys
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# customtkinter bundles theme JSON + image assets that it loads at runtime
ctk_datas = collect_data_files('customtkinter', include_py_files=False)

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('gui', 'gui'),
        *ctk_datas,
    ],
    hiddenimports=[
        'PIL._tkinter_finder',
        'cv2',
        'numpy',
        'requests',
        'dotenv',
        'tkinter',
        'tkinter.ttk',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'customtkinter',
        'pygrabber',
        'pygrabber.dshow_graph',
        'comtypes',
        'comtypes.client',
        'comtypes.stream',
        'comtypes._generate',
        'comtypes.typeinfo',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'scipy',
        'torch',
        'torchvision',
        'skimage',
        'IPython',
        'notebook',
        'pytest',
        'NDIlib',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TalesRunnerMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TalesRunnerMonitor',
)
