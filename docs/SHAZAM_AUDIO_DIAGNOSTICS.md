# Shazam「音声入力要求失敗」診断版 (2026-10-13)

## 目的

2026-10-10の収集ログで、`The Shazam button did not request the supplied audio` が578件（認識開始4,010件中14.4%）発生した。`No match` 1,721件は音質等で起こり得る別の事象であり、今回の異常として扱わない。

この版では、**音声入力要求の失敗原因を調べるためのログのみ追加**する。認識の開始順、15秒の通常の認識期限、10秒の音声要求期限、楽曲の2回確認、YouTube再生には変更を入れていない。

## 現行実装から分かること

- C# `BridgeHost.RecognizeAsync` はShazamのボタンをクリックしたあと、`audio-stream-started` WebMessageを10秒以内に受信できなければエラーを出す。
- したがって **このエラーだけで「Shazamサイトが一度も `getUserMedia` を呼ばなかった」とは証明できない**。たとえば呼んだがJS側でエラーになった場合、フックが差し替えられた場合、iframeに入った場合、ページ遷移・通知ロストでも起き得る。
- `app/services/shazam_webview_client.py` は `recognize_live()` 内の一般例外で `self.close()` している。つまり現在は音声要求失敗1件でもヘルパーが終了し、次回必要になったときに新しいヘルパーを起動する。
- `shazam_service.py` に「同じレーンの音声要求失敗2回で再生成」という別の監視はあるが、この一般例外時の強制終了とは独立している。今回のログには明示的な `Recycling WebView2 lane` はなかった。

## 新しいログ

通常ログレベルのまま、発生したエラーのときだけ以下を出力する。音声サンプル、認証情報、HTML本文、Webサイトのレスポンス本文は出さない。

- `Audio request timeout`: id/instance（レーン）、クリックからの経過ms、検出したボタンのラベル、C#側のブリッジ準備完了通知、PCM到着通知と初回音量、音声開始通知、Shazam `/tag/` HTTP応答件数/最後のステータス、ブラウザ本来の権限要求（アプリが拒否した件数と種別）。
- `Audio request page snapshot`: 認識が失敗する直前のページパス/読み込み状態/前面状態/iframe数/現在のクリック位置にある要素のタグと属性/JSブリッジの存在、`getUserMedia` フックが維持されているか、呼び出し・成功・失敗数、クリックイベントの到達数、PCM到着数など。
- `Recognition failed: ... id=... instance=...`: 失敗とC#のリクエストID、レーンを直接関連付ける。
- `helper close after error` / `helper spawn`: Pythonがいつ、どのレーンのヘルパープロセスを終了・起動したか。

ログの `id` は認識リクエストを突き合わせるためのランダムIDであり、Shazamの楽曲IDではない。

## 結果の判断

1. `bridgeArmed=True`, `pcmReached=True`, `getUserMediaCalls=0`, `clickEvents=0` : 音声はブラウザに到達しているが、クリックが現在のドキュメントには観測されていない。ボタン座標/重なり/クリックが届かない問題か、クリック後のページ遷移が疑われる。`cycle` が一致するかも確認。
2. `getUserMediaCalls=0`, `clickEvents>0`, `mediaHookInstalled=true` : クリックはJavaScriptのイベントになったが、Shazamは音声入力フックを呼んでいない。別のボタン、無効状態、同意ダイアログ、サイト側のJavaScriptエラーなどを確認。
3. `mediaHookInstalled=false` : サイト側のスクリプト等が `navigator.mediaDevices.getUserMedia` を後から差し替えた可能性。
4. `getUserMediaCalls>0`, `getUserMediaFailures>0` : 呼び出しはあったがJavaScript内で例外になった。`lastGetUserMediaError` と `contextState` を確認。
5. `getUserMediaSuccesses>0`, かつ `streamStarted=False` : JS側の成功とC#へのWebMessage通知が食い違う可能性。リクエストID、画面遷移、WebMessageの配送を追跡。
6. `nativePermissionsDenied>0` : JSフック経由でなくブラウザ本来のマイク権限要求が出ている可能性。レーンごとのWebView2/iframeを確認。
7. `pcmReached=False` : 音声配信側も確認する。ただし `getUserMedia()` の起動失敗の原因と同一とは限らない。
8. `Audio request page snapshot ... details=null` : ページ遷移やスクリプト実行失敗で、診断プローブ自身が失敗した可能性。C#側の最初のログは残る。

注意: `getUserMediaCalls`, `clickEvents` は**現在のページドキュメントでのカウンター**。認識途中にページ再読み込みが発生すると0から数え直す場合があるため、`bridge.cycle` と `path` を必ず一緒に確認すること。

## Windowsでの適用方法

ソース一式（README.md、`build.cmd` がある階層）へパッチZIPを展開上書きする。**既存EXEやDLLを差し替えるだけでは変更は反映されない**。`native` のC#を含むため、Windowsで次を実行し再ビルドする。

```powershell
cd 'C:\path\to\VJ_yattaro_source'
.\build.cmd
```

`build.cmd` はPython 3.12および .NET 8 SDK を必要とする（`tools/build.ps1` がビルドを実施）。会社ネットワークのプロキシや証明書によりパッケージのダウンロードが失敗する場合はビルドが未完了になる。その場合は生成済み `dist` のEXEに変更はない。

ログは `dist\VJ_yattaro\vj_yattaro.log`（またはソース実行時のプロジェクト直下）に保存される。最大約1000KiB・5世代で回転するため、長時間試験中は定期的にコピーする。

### 推奨する手動試験

**A. 通常動作で15〜30分程度（最初に実施）**

現場と同じ入力デバイス・レーン数（4）で使い、失敗ログが10件程度出たら `vj_yattaro.log` と `.1`〜`.5` を回収する。Windowsのマイク設定、音量の変更はしない（比較可能性のため）。診断ログは自動的に記録されるので `log_level=DEBUG` の変更は不要。

**B. 失敗原因がクリックかサイト状態か判別できなかった場合のみ**

アプリを一度終了し、PowerShellから次のように起動する（デバッグ時だけWebView2ウィンドウが表示される）。

```powershell
$env:VJ_SHAZAM_DEBUG = '1'
& 'C:\path\to\dist\VJ_yattaro\VJ_yattaro.exe'
```

- 「VJ_yattaro - Shazam WebView2 diagnostics (lane-N)」の **失敗したレーン**の画面を確認。画面が重なる場合はタイトルバーで移動する。
- 画面上に同意・クッキーダイアログ、CAPTCHA、ログイン、楽曲認識ボタン以外のオーバーレイ、ネットワークエラーが出ていないか確認する。
- 再現した直後の画面をスクリーンショットで保存する。画像にアカウント情報等があれば共有前に非表示にする。
- `F12` で開発者ツールを開ける。ConsoleにShazam側の例外があればエラーメッセージのみ記録する。Networkで `/tag/` が発生しているか、ステータスコードを確認する。**認証ヘッダー、Cookie、リクエスト本文、音声データは共有しない**。
- デバッグ画面の手動ボタンクリックは自動認識への干渉になるため、まずは観察のみにする。サイトから明示された同意操作が必要なときだけユーザー自身が操作する。

```powershell
Remove-Item Env:VJ_SHAZAM_DEBUG -ErrorAction SilentlyContinue
```

最後のコマンドで環境変数を戻し、アプリをいったん終了させて通常起動する。

## 変更したファイル

- `native/ShazamWebViewBridge/BridgeHost.cs`
- `native/ShazamWebViewBridge/Scripts/audio_bridge.js`
- `native/ShazamWebViewBridge/Scripts/audio_failure_snapshot.js` (新規)
- `app/services/shazam_webview_client.py`
- `tests/javascript_checks.cjs`
- この説明 `docs/SHAZAM_AUDIO_DIAGNOSTICS.md`

## 実施済み検証と制約

- Linux/Python 3.13環境でオフラインPython回帰テスト355件を実施済み。
- Node.js 22環境でJavaScriptモック試験29件を実施済み。
- .NET SDK/Windows/WebView2が現実行環境にないため、**C#のWindowsビルド・実際のShazamサイトの認識は未実施**。
