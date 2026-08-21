# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

shazam_datas, shazam_binaries, shazam_hiddenimports = collect_all('shazamio')
core_datas, core_binaries, core_hiddenimports = collect_all('shazamio_core')
sd_datas, sd_binaries, sd_hiddenimports = collect_all('sounddevice')
sddata_datas, sddata_binaries, sddata_hiddenimports = collect_all('_sounddevice_data')
retry_datas, retry_binaries, retry_hiddenimports = collect_all('aiohttp_retry')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=shazam_binaries + core_binaries + sd_binaries + sddata_binaries + retry_binaries,
    datas=[('web', 'web')] + shazam_datas + core_datas + sd_datas + sddata_datas + retry_datas,
    hiddenimports=shazam_hiddenimports + core_hiddenimports + sd_hiddenimports + sddata_hiddenimports + retry_hiddenimports,
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
    [],
    exclude_binaries=True,
    name='VJ_yattaro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VJ_yattaro',
)
