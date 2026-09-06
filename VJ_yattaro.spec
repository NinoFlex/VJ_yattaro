# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
helper = root / 'native' / 'ShazamWebViewBridge' / 'publish'
if not (helper / 'ShazamWebViewBridge.exe').is_file():
    raise SystemExit('Build native/ShazamWebViewBridge first (run build.cmd).')

datas = [(str(root / 'web'), 'web'), (str(root / 'assets'), 'assets'),
         (str(helper), 'shazam_webview')]
binaries = []
hiddenimports = []
for package in ('pygame', 'sounddevice', '_sounddevice_data'):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['shazamio', 'shazamio_core', 'aiohttp_retry'], noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='VJ_yattaro', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None, icon=[str(root / 'assets/vj_yattaro.ico')],
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='VJ_yattaro')
