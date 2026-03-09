# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Surgical Zooming.
# Build: pyinstaller SurgicalZooming.spec
# Produces a single-file, windowed Windows .exe (no console). Bundled assets
# are extracted to sys._MEIPASS at runtime; main.resolve_resource_path uses it.

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('settings.json', '.')],
    hiddenimports=[
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'pynput.keyboard._win32',
        'pynput.mouse._win32',
        'mss',
        'fire',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SurgicalZooming',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # Windowed/headless: no console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
