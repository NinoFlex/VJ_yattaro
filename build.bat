@echo off
echo VJ_yattaro Exe Build Starting...

echo 1. Cleanup
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo 2. Installing dependencies
python -m pip install -r requirements.txt
python -m pip install pyinstaller

echo 3. Building with PyInstaller
python -m PyInstaller --windowed --name="VJ_yattaro" --add-data="web;web" main.py

echo 4. Copying config file
copy config.json dist\VJ_yattaro\

echo 5. Copying web folder
xcopy web dist\VJ_yattaro\web /E /I

echo 6. Build completed
echo Folder: dist\VJ_yattaro\
echo Executable: VJ_yattaro.exe
echo.
echo Distribution folder contains:
echo - VJ_yattaro.exe (main executable)
echo - web/ (YouTube player folder)
echo - config.json (configuration file)
echo - _internal/ (internal libraries folder)
echo.
pause
