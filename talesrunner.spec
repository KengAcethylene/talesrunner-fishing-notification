# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for TalesRunner Fish Monitor (GUI)
#
# Build steps:
#   venv\Scripts\pip install pyinstaller
#   venv\Scripts\pyinstaller talesrunner.spec --clean
#
# Output: dist\TalesRunnerMonitor\TalesRunnerMonitor.exe
#
# The target machine must have NDI Tools Runtime installed:
#   https://www.ndi.tv/tools/
# (Processing.NDI.Lib.x64.dll is installed system-wide by it)

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[
        # Uncomment and adjust if NDI DLL is NOT on the system PATH:
        # (r'C:\Program Files\NDI\NDI 5 Runtime\Processing.NDI.Lib.x64.dll', '.'),
    ],
    datas=[
        ('templates', 'templates'),
        # config.json is auto-created on first run; include only if it already exists
        # ('config.json', '.'),
        ('gui', 'gui'),
    ],
    hiddenimports=[
        'NDIlib',
        'PIL._tkinter_finder',
        'cv2',
        'numpy',
        'requests',
        'dotenv',
        'tkinter',
        'tkinter.ttk',
        'tkinter.filedialog',
        'tkinter.messagebox',
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
    console=False,      # No console window for the GUI build
    # icon='icon.ico',  # Uncomment and provide an .ico file to set the app icon
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
