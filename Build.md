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
- `config.json` / `web` / `assets` の配置
- `shazam_history.json` の生成

```bat
build.bat
```

手動で実行する場合:

```powershell
py -3.12 -m PyInstaller --windowed --name="VJ_yattaro" --icon="assets\vj_yattaro.ico" --add-data="web;web" --add-data="assets;assets" --collect-all shazamio --collect-all shazamio_core --collect-all aiohttp_retry --collect-all sounddevice --collect-all _sounddevice_data main.py
Copy-Item config.json dist\VJ_yattaro\
Copy-Item -Recurse -Force web dist\VJ_yattaro\web
Copy-Item -Recurse -Force assets dist\VJ_yattaro\assets
[System.IO.File]::WriteAllText("dist\VJ_yattaro\shazam_history.json", "[]")
```

## ビルド後のファイル構成

```text
dist/VJ_yattaro/
├── VJ_yattaro.exe
├── web/
├── assets/
├── _internal/
├── config.json
└── shazam_history.json
```

`shazam_history.json` は実行時にも存在確認され、EXEと同じフォルダに保存されます。履歴は最大50件で、JSON配列として保存され、最新の認識が先頭になります。

```json
[
  {
    "timestamp": "2026-08-21 14:41:05",
    "title": "Track Title",
    "artist": "Artist"
  }
]
```

## Shazam機能の動作

- タイトルバーの `Rekordbox` / `Shazam` トグルで入力ソースを切り替えます。
- Rekordboxモードでは従来のDB履歴監視を使用します。
- ShazamモードではRekordbox監視を停止し、設定画面のShazamタブで選んだマイクを使用します。
- マイクは `mono / int16` で取得し、16 kHz対応なら16 kHzを使用します。非対応ならデバイスのネイティブ周波数（主に44.1/48 kHz）で取得し、Shazam判定直前に16 kHzへ軽量変換します。
- 最新8秒のみリングバッファに保持します。
- 3秒ごとに最新6秒をShazamへ送ります。
- Shazam通信中は次の認識要求を捨て、処理を蓄積しません。
- 前回と同じ `曲名 + アーティスト名` は履歴へ追加しません。
- Shazam結果から使用するフィールドは曲名 (`track.title`) とアーティスト名 (`track.subtitle`) のみです。

## 注意事項

1. Shazam認識にはインターネット接続が必要です。
2. 選択したマイクが16 kHzに対応しない場合は、デバイス既定のサンプルレートへ自動フォールバックします。
3. PyInstaller製EXEはアンチウイルスで誤検知される場合があります。
4. `config.json` には既存の個別設定が含まれるため、配布時の取り扱いに注意してください。


## Shazam入力デバイスが表示されない場合

まず、ビルドに使用したPython 3.12環境でPortAudioがWindowsの入力デバイスを列挙できるか確認します。

```powershell
py -3.12 -m sounddevice
```

入力デバイスがここに表示される場合、Python/PortAudio側の列挙は正常です。
修正版ではWindowsのHost APIを個別に走査し、1台のデバイス照会失敗で一覧全体が空にならないようにしています。
また、設定画面のShazamタブに検出件数または具体的なエラーを表示します。

EXEビルド後は次のDLLが存在することも確認してください。`build.bat` は自動確認します。

```text
dist\VJ_yattaro\_internal\_sounddevice_data\portaudio-binaries\libportaudio64bit.dll
```

`py -3.12 -m sounddevice` 自体で入力デバイスが出ない場合は、Windowsの「設定 > プライバシーとセキュリティ > マイク」でデスクトップアプリのマイクアクセスが許可されているか、デバイスマネージャー上で対象マイクが有効かを確認してください。
## A/Bプレイヤー操作パネル

- Player A / Player Bの操作パネルは、検索結果と履歴表の下側に表示します。
- 中央はA/Bのセグメントスイッチです。選択した側のパネル枠も強調され、表示・操作対象が同時に切り替わります。
- 左右の円形インジケーターは各物理プレイヤーの再生・一時停止操作です。`PLAYING` の間だけ60fps相当で回転し、それ以外の状態では停止します。
- 回転角は単調経過時間から毎フレーム算出するため、タイマー遅延や周回境界による引っ掛かりを蓄積しません。
- シーケンスバーは表示専用です。ブラウザからは状態変化時だけ現在位置と総時間を取得し、その後はアプリ側の単調時計で表示を進めます。
- `assets/vj_yattaro.ico` はWindows実行ファイル、タスクバー、ウィンドウへ適用するアプリアイコンです。
- `assets/vj_yattaro_icon.png` はアイコンの高解像度原稿です。

