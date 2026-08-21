$ErrorActionPreference = "Stop"

Write-Host "=== VJ_yattaro build start ===" -ForegroundColor Cyan

# 古いビルド成果物を削除
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

# PyInstaller
& py -3.12 -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name="VJ_yattaro" `
    '--add-data=web;web' `
    --collect-all shazamio `
    --collect-all shazamio_core `
    --collect-all aiohttp_retry `
    --collect-all sounddevice `
    --collect-all _sounddevice_data `
    main.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed. ExitCode=$LASTEXITCODE"
}

# 実行時に外出しで使うファイルをコピー
Copy-Item config.json dist\VJ_yattaro\ -Force
Copy-Item -Recurse -Force web dist\VJ_yattaro\web

Write-Host "=== Build complete ===" -ForegroundColor Green
Write-Host "dist\VJ_yattaro\VJ_yattaro.exe"
