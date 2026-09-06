"""Verify staged native dependencies, preserving existing user configuration/history."""
from pathlib import Path
import shutil


def main():
    root = Path(__file__).resolve().parents[1]
    stage = root / 'build' / 'staging' / 'VJ_yattaro'
    target = root / 'dist' / 'VJ_yattaro'
    helper = stage / '_internal' / 'shazam_webview'
    for name in ('ShazamWebViewBridge.exe', 'ShazamWebViewBridge.dll',
                 'ShazamWebViewBridge.runtimeconfig.json', 'coreclr.dll',
                 'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll'):
        if not (helper / name).is_file():
            raise RuntimeError(f'Native helper dependency not bundled: {helper / name}')
    if not list(helper.rglob('WebView2Loader.dll')):
        raise RuntimeError('WebView2Loader.dll was not bundled')
    audio = stage / '_internal' / '_sounddevice_data' / 'portaudio-binaries' / 'libportaudio64bit.dll'
    if not audio.is_file():
        raise RuntimeError(f'PortAudio DLL was not bundled: {audio}')
    if not (stage / 'VJ_yattaro.exe').is_file():
        raise RuntimeError('Application executable was not produced')
    target.parent.mkdir(parents=True, exist_ok=True)
    # No directory wipe: a failed copy (e.g. a running EXE) cannot delete settings.
    shutil.copytree(stage, target, dirs_exist_ok=True)
    for name in ('config.json', 'shazam_history.json'):
        if not (target / name).exists() and (root / name).is_file():
            shutil.copy2(root / name, target / name)
    if not (target / 'shazam_history.json').exists():
        (target / 'shazam_history.json').write_text('[]\n', encoding='utf-8')
    for name in ('web', 'assets'):
        shutil.copytree(root / name, target / name, dirs_exist_ok=True)
    print(f'Build complete: {target / "VJ_yattaro.exe"}')
    print('Existing config.json and shazam_history.json were preserved.')


if __name__ == '__main__':
    main()
