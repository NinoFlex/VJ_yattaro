@echo off
setlocal

echo === VJ_yattaro build start ===

if exist build (
    rmdir /s /q build
    if exist build (
        echo ERROR: build directory could not be removed.
        exit /b 1
    )
)

if exist dist (
    rmdir /s /q dist
    if exist dist (
        echo ERROR: dist directory could not be removed.
        echo VJ_yattaro.exe may still be running.
        exit /b 1
    )
)

py -3.12 -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --name="VJ_yattaro" ^
    --icon="assets\vj_yattaro.ico" ^
    --add-data="web;web" ^
    --add-data="assets;assets" ^
    --collect-all shazamio ^
    --collect-all shazamio_core ^
    --collect-all aiohttp_retry ^
    --collect-all pygame ^
    --collect-all sounddevice ^
    --collect-all _sounddevice_data ^
    main.py

if errorlevel 1 (
    echo ERROR: PyInstaller failed.
    exit /b 1
)

copy /y config.json dist\VJ_yattaro\ >nul
if errorlevel 1 (
    echo ERROR: Failed to copy config.json.
    exit /b 1
)

xcopy web dist\VJ_yattaro\web\ /e /i /y >nul
if errorlevel 1 (
    echo ERROR: Failed to copy web directory.
    exit /b 1
)

xcopy assets dist\VJ_yattaro\assets\ /e /i /y >nul
if errorlevel 1 (
    echo ERROR: Failed to copy assets directory.
    exit /b 1
)

echo === Build complete ===
echo dist\VJ_yattaro\VJ_yattaro.exe

endlocal
