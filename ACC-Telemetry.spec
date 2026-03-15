# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for ACC Telemetry
#
# Build command (run on Windows):
#   pip install pyinstaller
#   pyinstaller ACC-Telemetry.spec
#
# Output: dist/ACC-Telemetry.exe  (single file, no console window)
#
# Recorded track maps (tracks/*.json) are written NEXT TO the .exe at runtime,
# not bundled inside it, so they persist between sessions.

import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

# ── Collect everything PyQt6 and matplotlib need ────────────────────────────
pyqt6_datas,    pyqt6_binaries,    pyqt6_hiddens    = collect_all('PyQt6')
mpl_datas,      mpl_binaries,      mpl_hiddens      = collect_all('matplotlib')

# ── Hidden imports that PyInstaller misses with conditional imports ──────────
hidden = (
    pyqt6_hiddens
    + mpl_hiddens
    + [
        'matplotlib.backends.backend_qt5agg',
        'matplotlib.backends.backend_agg',
        # ACC shared memory (Windows-only, imported inside try/except)
        'pyaccsharedmemory',
        # iRacing SDK (Windows-only, imported inside try/except)
        'irsdk',
    ]
)

a = Analysis(
    ['test-listener.py'],
    pathex=[],
    binaries=pyqt6_binaries + mpl_binaries,
    datas=pyqt6_datas + mpl_datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim unused heavy packages to keep the EXE smaller
        'tkinter',
        'scipy',
        'numpy.distutils',
        'IPython',
        'pandas',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ACC-Telemetry',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # no black console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='icon.ico',  # uncomment and add icon.ico to use a custom icon
)
