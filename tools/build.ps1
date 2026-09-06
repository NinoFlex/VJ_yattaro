param([switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
function Invoke-Checked {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $File" }
}
try {
    Write-Host '=== VJ_yattaro / official Shazam WebView2 build ==='
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($launcher) {
            Invoke-Checked $launcher.Source @('-3.12', '-m', 'venv', (Join-Path $root '.venv'))
        } else {
            $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
            if (-not $systemPython) { throw 'Install 64-bit Python 3.12 with the Python launcher first.' }
            Invoke-Checked $systemPython.Source @('-c', 'import sys; sys.exit(0 if sys.version_info[:2] == (3,12) else 1)')
            Invoke-Checked $systemPython.Source @('-m', 'venv', (Join-Path $root '.venv'))
        }
    }
    Invoke-Checked $python @('-c', 'import sys, struct; sys.exit(0 if (sys.version_info[:2] == (3,12) and struct.calcsize(chr(80)) == 8) else 1)')
    if (-not $SkipInstall) {
        Write-Host '[1/4] Installing Python dependencies into .venv...'
        Invoke-Checked $python @('-m', 'pip', 'install', '-r', 'requirements.txt')
        Invoke-Checked $python @('-m', 'pip', 'install', 'pyinstaller>=6.11,<7')
    }
    Write-Host '[2/4] Building self-contained Windows WebView2 helper...'
    Invoke-Checked 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        (Join-Path $PSScriptRoot 'build-helper.ps1'))
    Write-Host '[3/4] Packaging application with PyInstaller...'
    # Stage the new build separately. Never delete the user's running/dist app first.
    Invoke-Checked $python @('-m', 'PyInstaller', '--noconfirm', '--clean',
        '--distpath', 'build\staging', '--workpath', 'build\pyinstaller', 'VJ_yattaro.spec')
    Write-Host '[4/4] Verifying and installing the distribution...'
    Invoke-Checked $python @('tools\finalize_build.py')
    Write-Host '[OK] dist\VJ_yattaro\VJ_yattaro.exe'
    Write-Host 'Distribute the entire dist\VJ_yattaro folder, not only the EXE.'
    Write-Host 'The Microsoft Edge WebView2 Runtime is required on the destination PC.'
} catch {
    Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
exit 0
