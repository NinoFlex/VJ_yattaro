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
%PY% -m PyInstaller --windowed --name="VJ_yattaro" --add-data="web;web" --collect-all shazamio --collect-all shazamio_core --collect-all sounddevice main.py
if errorlevel 1 exit /b 1

echo 4. Copying config file
copy /Y config.json dist\VJ_yattaro\ >nul

echo 5. Copying web folder
xcopy web dist\VJ_yattaro\web /E /I /Y >nul

echo 6. Creating Shazam history log
if not exist dist\VJ_yattaro\shazam_history.log type nul > dist\VJ_yattaro\shazam_history.log

echo 7. Build completed
echo Folder: dist\VJ_yattaro\
echo Executable: VJ_yattaro.exe
echo.
echo Distribution folder contains:
echo - VJ_yattaro.exe (main executable)
echo - web/ (YouTube player folder)
echo - config.json (configuration file)
echo - shazam_history.log (Shazam history, max 50 entries at runtime)
echo - _internal/ (internal libraries folder)
echo.
pause
endlocal
