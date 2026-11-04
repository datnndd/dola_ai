# -*- mode: python ; coding: utf-8 -*-
# App Qt6 (studio_qt.py) — giao diện duy nhất; không đóng gói tkinter/customtkinter.
from PyInstaller.utils.hooks import collect_all
import os

# The Codex shell adds document-tool DLL directories (libheif/poppler) to PATH.
# Their old ucrtbase.dll breaks QtCore in the packaged app on Windows 10.
# Use Windows/Python's own runtime dependencies, as when built from plain CMD.
os.environ['PATH'] = os.pathsep.join(
    entry for entry in os.environ.get('PATH', '').split(os.pathsep)
    if 'codex-runtimes' not in entry.casefold())

# Chrome, FFmpeg, the Pro extension and SKILL.md are intentionally shipped
# beside the EXE. Their hashes are compiled into bundle_integrity_data.py.
datas = []
binaries = []
hiddenimports = ['studio_core', 'studio_theme', 'studio_extension', 'task_status', 'proxyno1', 'proxyvn',
                 'dola_tabs', 'dola_limits', 'dola_fallback', 'element_recording', 'dola_media',
                 'drag_export', 'win32com', 'win32com.client', 'pythoncom', 'pywintypes',
                 'sheet_video', 'auto_jobs', 'google.oauth2.service_account',
                 'google.auth.transport.requests', 'bundle_integrity', 'bundle_integrity_data',
                 'seedance_browser', 'hidemium_browser', 'proxy_pool', 'proxyvn_api',
                 'seedance_video', 'seedance_30s', 'seedance_parallel']
for package in ('playwright', 'patchright'):
    tmp_ret = collect_all(package)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['studio_qt.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Qt modules the UI never imports (keeps the one-file EXE smaller).
    excludes=['tkinter', '_tkinter', 'customtkinter', 'studio', 'task_table', 'window_utils',
              'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
              'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.QtQuick', 'PySide6.QtQml',
              'PySide6.QtMultimedia', 'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtPdf'],
    noarchive=False,
    optimize=2,   # bỏ assert + docstring trong bytecode đóng gói
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SeedanceAuto',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
