@echo off
chcp 65001 >nul
echo VJ_yattaro Exeビルド開始...

echo 1. クリーンアップ
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo 2. PyInstallerでビルド
c:/Users/NINO/AppData/Local/Programs/Python/Python313/Scripts/pyinstaller.exe --windowed --name="VJ_yattaro" --add-data="web;web" main.py

echo 3. 設定ファイルをコピー
copy config.json dist\VJ_yattaro\

echo 4. webフォルダをコピー
xcopy web dist\VJ_yattaro\web /E /I

echo 5. ビルド完了
echo フォルダ: dist\VJ_yattaro\
echo 実行ファイル: VJ_yattaro.exe
echo.
echo 配布用のフォルダには以下のファイルが含まれています:
echo - VJ_yattaro.exe (メイン実行ファイル)
echo - web/ (YouTubeプレイヤー用フォルダ)
echo - config.json (設定ファイル)
echo - _internal/ (内部ライブラリフォルダ)
echo.
pause
