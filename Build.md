# VJ_yattaro - Shazam WebView2 build

この版はShazamIOを使用しません。旧版の `--collect-all shazamio` などのコマンドは不要です。

手順は [README.md](README.md) を参照してください。

```powershell
.\build.cmd
.\dist\VJ_yattaro\VJ_yattaro.exe
```

依存関係が導入済みの再ビルド:

```powershell
.\build.cmd -SkipInstall
```

ヘルパーだけビルドしてPythonで起動する場合:

```powershell
.\native\ShazamWebViewBridge\build.cmd
.\.venv\Scripts\python.exe main.py
```

Windows / Python 3.12 x64 / .NET SDK 8+ / Microsoft Edge WebView2 Runtime

実機での未確認項目は [VERIFICATION.md](VERIFICATION.md) に記載しています。
