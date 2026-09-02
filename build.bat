@echo off
setlocal

echo VJ_yattaro Exe Build Starting...
echo.
echo [INFO] ShazamIO Windows build uses Python 3.12 because shazamio-core does not provide a CPython 3.13 Windows wheel.

py -3.12 -c "import sys; print(sys.version)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python 3.12 was not found.
    echo Install 64-bit Python 3.12 and run this build again.
    exit /b 1
)

set PY=py -3.12

echo 1. Cleanup
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo 2. Installing dependencies
%PY% -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
%PY% -m pip install pyinstaller
if errorlevel 1 exit /b 1

echo 3. Building with PyInstaller
%PY% -m PyInstaller --windowed --name="VJ_yattaro" --add-data="web;web" --add-data="assets;assets" --collect-all shazamio --collect-all shazamio_core --collect-all aiohttp_retry --collect-all pygame --collect-all sounddevice --collect-all _sounddevice_data main.py
if errorlevel 1 exit /b 1

echo 4. Verifying Shazam runtime dependencies
%PY% -c "import aiohttp_retry, shazamio, shazamio_core; print('Shazam runtime imports OK')"
if errorlevel 1 exit /b 1

echo 5. Verifying PortAudio bundle
if not exist dist\VJ_yattaro\_internal\_sounddevice_data\portaudio-binaries\libportaudio64bit.dll (
    echo [ERROR] PortAudio DLL was not bundled.
    echo Expected: dist\VJ_yattaro\_internal\_sounddevice_data\portaudio-binaries\libportaudio64bit.dll
    exit /b 1
)

echo 6. Copying config file
copy /Y config.json dist\VJ_yattaro\ >nul

echo 7. Copying web folder
xcopy web dist\VJ_yattaro\web /E /I /Y >nul

echo 8. Copying assets folder
xcopy assets dist\VJ_yattaro\assets /E /I /Y >nul

echo 9. Creating Shazam history JSON
if not exist dist\VJ_yattaro\shazam_history.json echo []> dist\VJ_yattaro\shazam_history.json

echo 10. Build completed
echo Folder: dist\VJ_yattaro\
echo Executable: VJ_yattaro.exe
echo.
echo Distribution folder contains:
echo - VJ_yattaro.exe (main executable)
echo - web/ (YouTube player folder)
echo - assets/ (UI animation assets)
echo - config.json (configuration file)
echo - shazam_history.json (Shazam history, max 50 entries at runtime)
echo - _internal/ (internal libraries folder)
echo.
pause
endlocal
