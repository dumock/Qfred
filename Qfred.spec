# -*- mode: python ; coding: utf-8 -*-
import os
import PyQt6

qt_plugins = os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6', 'plugins')

a = Analysis(
    ['qfred_pyqt.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('q_logo.png', '.'),
        ('q_logo_hd.ico', '.'),
        (os.path.join(qt_plugins, 'platforms'), 'PyQt6/Qt6/plugins/platforms'),
        (os.path.join(qt_plugins, 'styles'), 'PyQt6/Qt6/plugins/styles'),
    ],
    hiddenimports=['PyQt6.sip', 'PyQt6.QtCore', 'PyQt6.QtGui', 'PyQt6.QtWidgets'],
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
    name='Qfred',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['q_logo_hd.ico'],
)
