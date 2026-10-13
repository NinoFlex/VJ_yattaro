# YouTube自動再生フォールバック／プリロード停滞の復旧（2026-10-13）

## 目的と変更内容

前回の **Shazam音声要求失敗の診断機能**を維持し、YouTube再生に対して以下を追加しています。

1. **自動再生中に最初の候補がエラーを返したら、次の検索順位の動画へ切り替える。**
   - 通常モードでは1位がエラー → 2位へ切り替えて `PRELOAD` → `ready` → `PLAY` の順に実行します。
   - 再生不能判定はブラウザプレイヤーからの `state=error` （例: YouTube Error 100/101/150/153）を利用します。
   - 失敗が繰り返されても候補を延々と切り替えないよう、**1回だけ**次順位へ移ります。2位も失敗したら自動フォールバックを止めます。
   - アニメOPモードは従来の長さによる優先順位選択を維持し、選ばれた動画が失敗した場合は「選ばれた順位の次」に切り替えます。
   - 検索結果の表示順は変えません。切り替え先を選択表示します。
   - 手動検索・手動再生・手動プリロード時は過去の自動フォールバックを解除し、意図しない動画切り替えを防ぎます。

2. **Shazamでアーティスト名が空欄の場合も自動再生の対象にする。**
   - 従来の「Shazam title-only → allow_auto_play=False」制限だけを解除します。
   - 自動再生スイッチがOFFなら再生しません。履歴の手動選択など、`allow_auto_play=False` での検索も変わりません。
   - 検索キーワードは従来どおり、取得できた曲名をベースとします。曲名だけなので、誤った動画が検索結果1位に来るリスクはあります。

3. **プリロードが15秒以上 `ready` にならない場合は再送する。**
   - コントローラーが `PRELOAD` をキューへ入れた時点から、単発15秒のタイマーを開始します。
   - `ready` / `playing` / `error` の通知で、同じ動画IDの監視を終了します。別動画の遅れた通知では終了しません。
   - 15秒停滞で `PRELOAD(forceReload=true)` を1回再送し、ブラウザは `stopVideo()` → `cueVideoById()` で実際の読み込みを再試行します。
   - さらに15秒停滞したら2回目を再送します。計45秒進展がなければ監視を打ち切ります。
   - 自動再生中は次順位へフォールバックします。手動プリロード中は次動画を勝手に選びません。
   - ブラウザ側の従来「1秒でreadyを通知するフォールバック」は、YouTubeの状態が確認できた場合に限定しました。実際には未準備なのに監視を解除することを避けます。

## ログで確認する文字列

```text
UI: Auto-play recovery armed video=<first_id> fallback=<second_id>
UI: Auto-play fallback after browser-error code=150: <first_id> -> <second_id>
UI: PRELOAD still pending after 15s; retry=1/2 video=<video_id> forceReload=true
UI: PRELOAD still pending after 15s; retry=2/2 video=<video_id> forceReload=true
UI: PRELOAD timed out after 2 retries (45s): <video_id>
PlayerHttpServer: Queued command PRELOAD ... video=<video_id>
```

ブラウザConsoleには `Forced PRELOAD retry for ...` および `PRELOAD still awaiting YouTube readiness ...` などが出力されます。ログの `No match` は、Shazamが曲を識別できなかったことを表し、上記の再生復旧とは独立しています。

## Windowsでの動作確認例

1. 診断版ソースへ差分パッチを適用した状態、または同梱の更新ソース一式から `build.cmd` を実行してアプリを起動します。
2. 「自動再生ON」でShazamモードまたはRekordboxモードから曲を検出します。
3. 1位動画が再生不能を返した際、ログに `Auto-play fallback` が出て2位動画に切り替わることを確認します。
4. 曲名のみになったShazam履歴を自動検出した場合にも、YouTube検索後に `PRELOAD` が送信されることを確認します。
5. ネットワークの遅い状況などで `PRELOAD` が停滞した場合、約15秒後に1回目、約30秒後に2回目の再送を確認します。**故意にShazam・YouTubeのサービスを不正操作したり、権限・認証を回避したりする必要はありません**。
6. プレイヤー側ログを確認するにはF12のConsoleを利用できます。ブラウザが別タブの古いセッションを参照している場合は、アプリからプレイヤーを開き直してください。

## オフラインテスト

```powershell
python -m unittest discover -s tests -p "test_*.py" -q
node tests/javascript_checks.cjs
node tests/player_recovery_checks.cjs
```

注意: オフライン試験ではQt/YouTube/Shazam/マイクの実動作を保証しません。Windowsの実プレイヤーで確認が必要です。
