# Shazam先行検索の状態管理：B確定再生とC仮認識の分離

## 対象

`main.py` と新設した `app/models/` に、Qtと独立した型付き状態を追加しました。
Shazam認識方法・2回一致判定・YouTube検索順位・再生タイムアウトの値は変更していません。

| モデル | ファイル | 管理内容 |
|---|---|---|
| `TrackCandidate` | `app/models/track_candidate.py` | 1回目の認識候補、確定・履歴反映、再生許可、PRELOAD状態、曲情報 |
| `SearchRequest` | `app/models/search_request.py` | 検索対象曲ID、検索中・保留・完了・取消・失敗 |
| `PlaybackAttempt` | `app/models/playback_state.py` | Shazam候補ID、動画ID、PRELOAD/PLAY要求ID、再送回数、再生状態 |
| `ShazamPlaybackState` | `app/models/shazam_state.py` | 仮候補C、確定再生待機B、物理PRELOAD所有者の独立管理と状態遷移 |

`MainWindow` の従来名 `_shazam_preview` と `_confirmed_shazam_pending` は
`ShazamStateMixin` の**プロパティ**です。状態は二重保存せず、同じ
`ShazamPlaybackState` の `tentative` / `confirmed_pending` を参照します。
`TrackCandidate` は新規コードでは型付き属性を使用します。既存の呼び出し口・一部の
AST式テストと互換性を保つための辞書形式アクセスのみ残しています。

## 保証する不変条件

1. **1回目のC認識でBの再生所有権を変更しない**。Bが2回一致済みで
   `PLAYING` 未到達なら、`confirmed_pending` とその `PlaybackAttempt` はBのまま。
2. CのYouTube検索結果は受け取るが、B再生待機中はCの物理PRELOADを**保留**する。
   `begin_preload(C, ...)` 自体も、状態管理層が所有権を検査して拒否する。
3. Cの誤認識取消はCの候補・保留検索だけを破棄する。Bが所有する
   `pending_play_video_id`、PRELOAD再送監視、PLAY許可、要求IDを変更しない。
4. Bのready / playingは**Bに割り当てた要求IDと動画IDの両方**に一致した場合だけ
   Bの `PlaybackAttempt` を更新する。同じ動画IDを選んだCや古い再送要求による
   誤った状態進行を防ぐ。
5. Cが**2回一致**した場合のみ、Bの未完了再生待機を置き換え、Cの保留PRELOADを開始できる。
6. BがPLAYINGに達した後は、Cの仮候補のPRELOADを実行してよいが、
   Bの `PlaybackAttempt` は `PLAYING` のまま保持する。
7. 検索・PRELOAD・PLAYの完了順序が入れ替わっても、仮候補はPLAY権限を得ない。

## 状態遷移

| 時系列 | 仮登録 | 確定済みPLAY待機 | PRELOAD所有者 | 動作 |
|---|---|---|---|---|
| B初回認識 | B | なし | B（許可） | 検索、PRELOAD、PLAY禁止 |
| B 2回目一致 | B（確定） | なし | B | PLAY許可。readyなら即PLAY、未準備なら待機 |
| C初回認識 | C | B | B | C検索のみ、C PRELOAD保留 |
| C不一致・取消 | なし | B | B | C関連だけを破棄。Bを再生継続 |
| B PLAYING | C（残っていれば） | なし | Cへ移譲可能 | Bを再生中と確定、Cの保留PRELOAD開始 |
| C 2回目一致 | C（確定） | Bを終了 | C | CのPRELOAD/PLAYを優先 |

CとBの**YouTube動画IDが偶然同じ**でも、候補IDと要求IDに基づいて判定します。

## 動作の確認

Windows上でアプリを実際に起動できる場合、次の順序を確認してください。

1. Shazamモードと自動再生をONにする。
2. Bを2回一致で確定し、BのPRELOADを回線遅延などで待機させる。
3. Cを1回だけ誤認識させ、Cの仮登録は出るがBの待機が維持されることを確認する。
4. Cが取り消された後にBのreadyを発生させ、BがPLAYすることを確認する。
5. Cの検索結果がBのreadyより先に届く場合、Bの動画IDとCの動画IDが同じ場合、
   Bの15秒PRELOAD再送中にCを認識する場合も確認する。
6. 今度はCを2回一致させ、Bの未完了待機からCへ切り替わることを確認する。

オフラインテスト：

```powershell
python -m pytest -q tests/test_shazam_state_ownership.py
python -m pytest -q tests
node tests/javascript_checks.cjs
node tests/player_recovery_checks.cjs
node tests/player_race_checks.cjs
```

`tests/test_shazam_state_ownership.py` では、B/Cの同じ動画ID、古い要求ID、
C検索完了の割り込み、Cの取り消し、Bの再送要求ID更新、B再生開始後のC PRELOAD、
Cの正式確定時の所有権移動を確認します。

## 留意点

- この変更は**Shazam確定前後の状態と物理PRELOAD所有権のモデル化**です。
  手動検索やMIDI操作に使用する既存のUI用状態変数までは完全置換していません。
  将来的に `PlaybackController` へ分離する場合は、このモデルを再利用できます。
- Python側には候補IDとコマンド要求IDを検証する処理がありますが、実Shazamサイトの
  応答、WebView2の実動作、実際のYouTube IFrameによる動画再生はオフライン検証の範囲外です。
- 実機で同一の動画IDが再利用される場合、ブラウザが `requestId` を必ず返すことが
  重要です。旧ブラウザタブは閉じ、最新 `web/player.js` で再起動してください。


## 追加: 確定済み検索の優先保護

B曲が2回一致していてもYouTube検索がまだ実行されていない場合、C曲の
1回目認識でB検索を上書きしないよう`queued_search`と`speculative_search`
を分離しています。C曲2回目一致ならBの未完了検索をキャンセルしてCを優先します。
また、遅延した前検索スレッドのシグナルは送信元を照合して拒否します。
詳しくは[検索キュー優先度](SEARCH_QUEUE_PRIORITY.md)を参照してください。
