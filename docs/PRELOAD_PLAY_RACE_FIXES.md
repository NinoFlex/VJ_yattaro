# Shazam先行検索・動画切り替えの競合対策（問題②③④）

## 適用元と変更範囲

`VJ_yattaro_diagnostics_speculative_preload_source.zip` を基に、`main.py`、`web/player.js` と回帰テストを変更した版です。Shazam音声要求診断、初回認識による仮登録・先行検索、15秒間隔のPRELOAD再送、1位再生失敗時の2位切り替えは維持しています。

### ② PRELOADを誤って完了扱いしない

- A/BそれぞれのPRELOADに要求識別子（`commandId`→`requestId`）と内部世代番号を割り当てます。
- `playerVideoIds` に保持している「要求したID」ではなく、YouTube iframe APIが実際に返す `getVideoData().video_id`（取得可能なら動画URLでも照合）と `CUED` / `PAUSED` / `PLAYING` 状態を確認してから `ready` を報告します。
- 古い動画の `CUED` イベントを除外し、1秒後の補助確認でも動画IDと実際の状態を照合します。
- Python側も `requestId` を照合し、別プレイヤー・古いPRELOAD再送の通知で15秒監視を解除しません。
- iframeフォールバックは、DOMへ挿入しただけでは `ready` にせず、iframeの `load` イベントを待ちます。ただし、iframeのloadは**実際の動画再生可能性を保証しない**ため、PLAY開始監視は続行します。

### ③ PLAYの無限待機を終了する

- PLAY後のYouTube準備確認（従来無期限の`waitForReadyAndSwitch`）を最大12秒に変更。期限切れで `play-ready-timeout` としてエラー通知します。
- 画面切り替え後、YouTubeの実際の `PLAYING` に到達するまで最大12秒監視。未到達なら `play-start-timeout` として通知します。フェード完了だけでは再生成功扱いしません。
- Python側でもPLAY要求を30秒監視し、ブラウザ応答が途切れた場合に復旧処理を行います。
- 自動再生中は、1位の失敗で2位へのフォールバックを行います。2位も失敗したら停止し、無限に動画を切り替えません。
- 要求IDの不一致により古いエラー・古い再生開始通知を破棄し、新しい操作へ割り込ませません。

### ④ 確定済みB曲のPLAY待機をC曲の仮認識から守る

- B曲が2回一致済み、かつまだ実際の再生開始通知を受けていない場合、B曲の再生待機情報を別の状態として保持します。
- 続くC曲が1回だけ認識されても、BのPRELOAD・PLAY・フォールバックを取り消しません。
- C曲の検索は並行して進めます。ただし同一の物理PRELOAD先への干渉を避けるため、C曲のPRELOADはB曲の再生開始または失敗確定後に実行します。
- C曲が誤認識として取り消されたらCの仮登録だけを削除します。B曲の自動再生権限は維持します。
- C曲も2回一致して正式確定した場合には、C曲を優先してB曲の未実行再生待機を終了します。
- B曲のYouTube検索がC曲の初回認識より遅れて完了しても、Bの結果を取り違えずに処理します。

## Windows実機での確認手順

1. ソース一式を展開し、`build.cmd` でビルドします（本配布ZIPにビルド済みexeは含みません）。
2. 動作している古いプレイヤーのブラウザタブを閉じ、アプリからプレイヤーを新規起動してください。旧タブでキャッシュされた`player.js`が残ると変更が反映されません。
3. Shazamモード・自動再生ONで、曲AからBへ切り替えます。Bを1回認識した時点では仮登録とPRELOADまで、2回目一致後にPLAYとなることを確認します。
4. B曲のYouTube検索やPRELOADの最中にC曲が1回だけ誤認識される状況を再現し、B曲が再生開始できること、C曲の仮登録だけが消えることを確認します。
5. 回線を意図的に遅延させるかオフラインにして、PRELOAD時の15秒・30秒再送と45秒での失敗判定、PLAY時の約12秒＋12秒（ブラウザ）・30秒（アプリ側）の期限切れと2位への移行を確認します。時間はネットワーク状態により実際のログ出力位置が前後します。
6. A/B双方で同じYouTube動画IDを扱い、古いA側の状態通知がB側の準備完了扱いにならないことを確認します。
7. `vj_yattaro.log` と、必要ならブラウザの開発者ツールConsoleを採取します。ログには`Ignoring stale PRELOAD feedback`、`PLAY start watchdog armed`、`PLAY did not reach browser PLAYING`、`Preserving confirmed Shazam PLAY`、`Releasing deferred Shazam PRELOAD`などの診断メッセージが出ます。

## オフライン回帰試験

```powershell
python -m unittest discover -s tests -p "test_*.py" -q
node tests/javascript_checks.cjs
node tests/player_recovery_checks.cjs
node tests/player_race_checks.cjs
node --check web/player.js
```

確認時点：Python 414件、Shazam JavaScript 29件、既存プレイヤー JavaScript 5件、新規レアケース JavaScript 8件が通過しています。

**未確認事項:** この環境ではWindows/.NET/WebView2、Shazamサイトへの実接続、YouTube iframe APIの実ネットワーク応答による再生試験は行えていません。特にiframeフォールバックはYT iframe APIからの`PLAYING`を取得できないため、実再生していても12秒の起動監視で失敗扱いになる可能性があります。Windows実機で確認してください。
