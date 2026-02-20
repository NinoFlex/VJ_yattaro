# VJ_yattaro Exeビルド手順

## 前提条件

### 必要なライブラリのインストール
```bash
pip install PySide6 requests pyrekordbox pyinstaller
```

## ビルドコマンド

### 基本ビルド（単一exeファイル）
```bash
pyinstaller --onefile --windowed --name="VJ_yattaro" main.py
```

### 推奨ビルド（フォルダ形式 - 起動が高速）
```bash
pyinstaller --windowed --name="VJ_yattaro" --add-data="web;web" main.py
```

### 完全ビルド（アイコン設定付き）
```bash
pyinstaller --windowed --name="VJ_yattaro" --add-data="web;web" --icon=icon.ico main.py
```

## ビルドオプション説明

- `--onefile`: 単一exeファイルにまとめる（起動が遅くなる）
- `--windowed`: コンソールウィンドウを非表示
- `--name="VJ_yattaro"`: 出力ファイル名
- `--add-data="web;web"`: webフォルダをexeに含める
- `--icon=icon.ico`: アイコンファイル設定（任意）

## ビルド後のファイル構成

### フォルダ形式の場合
```
dist/VJ_yattaro/
├── VJ_yattaro.exe          # メイン実行ファイル
├── web/                     # webフォルダ（自動コピー）
├── _internal/               # 内部ライブラリ
└── config.json             # 設定ファイル（手動配置）
```

### 単一exeの場合
```
dist/
├── VJ_yattaro.exe          # メイン実行ファイル
└── web/                    # webフォルダ（手動配置）
```

## 配置手順

### 1. ビルド実行
```bash
# 推奨：フォルダ形式でビルド
pyinstaller --windowed --name="VJ_yattaro" --add-data="web;web" main.py
```

### 2. ファイル配置
```bash
# ビルド結果のフォルダに移動
cd dist/VJ_yattaro/

# config.jsonをコピー（既存の設定ファイルを利用）
copy ..\..\config.json .

# webフォルダの確認（自動でコピーされているはず）
# もしコピーされていない場合は手動でコピー
# copy ..\..\web . /E
```

### 3. 実行確認
```bash
# exeを実行して動作確認
VJ_yattaro.exe
```

## 自動配置バッチファイル

### build.bat（Windows用）
```batch
@echo off
echo VJ_yattaro Exeビルド開始...

echo 1. クリーンアップ
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo 2. PyInstallerでビルド
pyinstaller --windowed --name="VJ_yattaro" --add-data="web;web" main.py

echo 3. 設定ファイルをコピー
copy config.json dist\VJ_yattaro\

echo 4. ビルド完了
echo フォルダ: dist\VJ_yattaro\
echo 実行ファイル: VJ_yattaro.exe
pause
```

## 注意事項

1. **webフォルダ**: YouTubeプレイヤーのHTML/JSファイルが必須
2. **config.json**: APIキーなどの個別設定を含むため別途配置
3. **アンチウイルス**: PyInstaller製exeは誤検知される場合あり
4. **初回起動**: ライブラリ展開のため時間がかかる場合あり

## トラブルシューティング

### webフォルダが見つからないエラー
- `--add-data="web;web"`オプションを確認
- 手動でwebフォルダをexeと同じ階層に配置

### config.jsonが見つからないエラー
- exeと同じ階層にconfig.jsonを配置
- 初回起動時に設定ダイアログが表示される場合は正常

### 起動が遅い場合
- `--onefile`を外してフォルダ形式でビルド
- SSDでの実行を推奨