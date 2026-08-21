# VJ_yattaro Exeビルド手順

## 前提条件

Shazam機能を含むWindows版は **64-bit Python 3.12** でビルドしてください。
`shazamio 0.8.1` が利用する `shazamio-core 1.1.2` は、Windows向けCPython 3.13 wheelが提供されていないためです。

必要ライブラリは `requirements.txt` に含めています。

```powershell
py -3.12 -m pip install -r requirements.txt
py -3.12 -m pip install pyinstaller
```

## 推奨ビルド

通常は `build.bat` を実行してください。以下を自動で行います。

- Python 3.12の確認
- 依存ライブラリのインストール
- PyInstallerによるフォルダ形式ビルド
- `config.json` / `web` の配置
- `shazam_history.log` の生成

```bat
build.bat
```

手動で実行する場合:

```powershell
py -3.12 -m PyInstaller --windowed --name="VJ_yattaro" --add-data="web;web" --collect-all shazamio --collect-all shazamio_core --collect-all sounddevice main.py
Copy-Item config.json dist\VJ_yattaro\
Copy-Item -Recurse -Force web dist\VJ_yattaro\web
New-Item -ItemType File -Force dist\VJ_yattaro\shazam_history.log
```

## ビルド後のファイル構成

```text
dist/VJ_yattaro/
├── VJ_yattaro.exe
├── web/
├── _internal/
├── config.json
└── shazam_history.log
```

`shazam_history.log` は実行時にも存在確認され、EXEと同じフォルダに保存されます。履歴は最大50件で、最新の認識がファイル末尾になる時系列形式です。

```text
2026-08-21 14:41:05 | Artist | Track Title
```

## Shazam機能の動作

- タイトルバーの `Rekordbox` / `Shazam` トグルで入力ソースを切り替えます。
- Rekordboxモードでは従来のDB履歴監視を使用します。
- ShazamモードではRekordbox監視を停止し、設定画面のShazamタブで選んだマイクを使用します。
- マイクは `16 kHz / mono / int16` で取得します。
- 最新8秒のみリングバッファに保持します。
- 3秒ごとに最新6秒をShazamへ送ります。
- Shazam通信中は次の認識要求を捨て、処理を蓄積しません。
- 前回と同じ `曲名 + アーティスト名` は履歴へ追加しません。
- Shazam結果から使用するフィールドは曲名 (`track.title`) とアーティスト名 (`track.subtitle`) のみです。

## 注意事項

1. Shazam認識にはインターネット接続が必要です。
2. 選択したマイクが16 kHz / mono / int16入力に対応していない場合は、Shazamモード開始時にエラーになります。その場合は別の入力デバイスまたは「システム既定」を選択してください。
3. PyInstaller製EXEはアンチウイルスで誤検知される場合があります。
4. `config.json` には既存の個別設定が含まれるため、配布時の取り扱いに注意してください。
