# テスト手順と今回の確認結果

確認日: 2026-10-13

## オフライン回帰テスト

Windows用の開発環境では、ルートフォルダーで実行します。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
node tests/javascript_checks.cjs
node tests/player_recovery_checks.cjs
node tests/player_race_checks.cjs
```

PythonのテストはQtと通信をモック化しています。実機の認識試験ではありません。
`test_track_matching.py`自体は標準ライブラリーだけで実行でき、
既存の統合テストには`numpy`と`requests`も必要です。

今回、Linux上のPythonとNode.jsで次を確認しました。

| 確認対象 | 結果 |
|---|---|
| Pythonの回帰テスト | 505件成功 |
| Shazam向けJavaScriptモック試験 | 29件成功 |
| YouTube PRELOAD復旧JavaScriptモック試験 | 5件成功 |
| YouTubeプレイヤー競合JavaScriptモック試験 | 8件成功 |
| JavaScriptの構文検査 | `web/player.js` 成功 |

入力として添付された個人の履歴JSONは書き換えていません。
そのファイル自体もソースZIPには収録していません。

主な既存テストは、本家とカバーの交互検出、アーティスト相違、3文字一致と4文字一致の境界、
4文字未満の従来判定、同曲が別曲を挟んで再登場する場合、古い認識結果の拒否、
再起動時の初回通知、履歴の読み込みと保存、全グループ使用中の音声変換抑止です。

### 2026-10-13 現場ログの回帰テスト（仮表示維持は撤回）

`tests/test_shazam_field_log_regressions.py` で11件を追加しました。
ID-onlyで2回一致がメタデータより先行する経路、同じ曲の時系列追い越し、
欠番での仮表示期限切れ、古い別曲候補による検索抑制、即時確定時の表示遷移、
誤認識時の正しい破棄、候補ID重複通知抑制などを確認しています。
詳細: [FIELD_LOG_20261013_FIXES.md](FIELD_LOG_20261013_FIXES.md)

### Shazam公開曲ページのアーティスト名補完

`test_shazam_primary_page_artist.py` に11件の単体テストを追加しました。
保存Shazamページの専用ヘッダー、CSSクラスのハッシュ変化、異なる曲ID/名前空間、
二重ヘッダー、古い曲名、canonicalとog:urlの矛盾、UI宣伝の除外を確認します。
曲の2回一致・検索・PRELOAD制御には変更していません。
詳細: [SHAZAM_TRACK_ARTIST_RECOVERY.md](SHAZAM_TRACK_ARTIST_RECOVERY.md)

### Shazam旧 `/track/` 形式・アーティスト補完回帰（汎用）

`tests/test_shazam_primrose_recovery.py` で、Shazam一次ページヘッダーからの取得、
任意の別ID・アーティストへの同一処理、別曲へのHTTPリダイレクト時の空欄維持、
同じIDとタイトルの照合、別ID・`/song`の除外、ライブ情報の優先、
canonical URL再試行を検証します。曲ID→アーティストの個別固定テーブルは廃止。
C#のWebView2描画待ちは `tests/test_route_id_fast_return.py` のソース契約テストで確認。
詳細: [PRIMROSE_CEUI_RECOVERY.md](PRIMROSE_CEUI_RECOVERY.md)

### Shazam 4レーン共有プール・時系列確認の追加確認

`ShazamService`をQt/WebView2だけ模擬して確認しています。

- 4レーン・3秒間隔の定数を維持すること。
- 1回目の有効結果だけでは履歴を更新せず`Candidate pending`になること。
- 次の有効結果が既存のタイトル前方一致ルールで同一曲なら確定すること。
- 異なるタイトルが1回だけ入っても確定されないこと。
- `A → X → A`で単発の`X`を履歴追加しないこと。
- 通常の認識完了順が前後しても要求連番`seq`順で判定すること。
- head `seq`が未応答でも、後続の連続2 `seq`が同一曲ならその一致ペアでheadを追い越して確定できること。
- 後続が1件だけ、または後続2件が不一致ならheadを追い越さないこと。
- head追い越し後に古い結果が返っても履歴や現在曲を巻き戻さないこと。
- round-robin上の優先レーンがbusyでも、別の空きレーンへ認識を投入すること。
- 4レーンすべてbusyの場合は要求連番とスロットを消費せず待機すること。
- 待機中にworkerが空けば、そのworkerをライブPCM配信へ接続して再開すること。
- busy解消直後にcatch-upで認識を連続投入せず、実投入時刻から3秒空けること。
- Shazam offsetに依存せず、従来の`is_same_track()`だけで2回確認すること。

### YouTube検索の追加確認

前回の79件に加え、34件の検索回帰テストを追加しました。
提示された曲名とアーティストの結合文字列を使い、
自動検索と手入力から「元の指定」「曲名のみ」「付加情報なしの曲名」へ進むことを確認しました。
実際の`main.py`の検索メソッドも、GUI部分のみを模擬して実行しています。

初回成功時の追加検索抑止、最大3種類までの打ち切り、重複・空検索の除外、
通信エラー時の打ち切り、中断時の追加通信抑止、既存のAPIキー切替、
60秒未満の動画しかない場合の再検索、通常の副題・カスタムテンプレートの維持も対象です。

通信は模擬応答です。YouTubeで実際にヒットすることは未確認です。
以上は前回追加した検索テストで、今回も再実行して通過しています。

今回の変更では、Shazam履歴のアーティスト名が空の場合も、自動検出からの検索は
自動再生を抑制しないことを確認しています。手動検索の自動再生抑止は維持しています。

### アニメOPモードの追加確認

`test_autoplay_selection.py`に45件を追加しました。このファイルは標準ライブラリーだけで実行できます。

- 上位4件の判定、1:30・1:45を含む境界、5位以下の除外、複数該当時の順位優先。
- 長さ不明・不正値・空の結果、該当なしなら1位に戻ること。
- 自動再生OFF時のモード解除・操作禁止・保存値の修正、再起動時の復元。
- 手動検索での自動再生抑止、優先動画へのPRELOAD→ready→PLAY、選択表示の一致。

選曲関数と`main.py`の実際のメソッドを実行し、Qtボタン・リスト・設定保存・プレイヤーは模擬しています。
この環境ではPySide6を導入できなかったため、実画面の表示・クリック操作は未確認です。
今回の再生復旧では`web/player.js`を変更しています。`ui/`とShazamネイティブブリッジは変更していません。
以前の4レーン化やoffset判定に関する変更は、今回の追加修正対象には含みません。

### 自動再生失敗・PRELOAD再送の追加確認

- 1位の再生エラー（100/101/150/153等）で2位がプリロードされ、`ready`を受けたら再生されること。
- 2位もエラーになったら、それ以上の自動切り替えをしないこと。
- 手動操作後の古いエラーで自動切り替えしないこと。
- 15秒経過による`forceReload=true`の再送と、最大2回の制限。
- 新しい動画のPRELOAD監視を、古い動画の`ready`通知で解除しないこと。
- JavaScript側が同じ動画IDでも実際に再キューすること、未完了なのに偽の`ready`を返さないこと。
- 前回のShazam音声要求失敗診断コードと資料を変更していないこと。

ブラウザでの実再生、WindowsのWebView2動作についてはオフラインテストの対象外です。
詳細は[再生復旧の仕様](PLAYBACK_RECOVERY.md)を参照してください。

## 実ブラウザーの補助テスト

実行できる環境でのみ、任意に利用してください。

```powershell
.\.venv\Scripts\python.exe -m pip install playwright
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe tests/browser_checks.py
```

ブラウザー用テストはlocalhost上の模擬ページを使うもので、
実Shazamサイトの認識精度を測るものではありません。
前回の確認では`http://localhost:8765/ja-jp`への遷移が
`net::ERR_BLOCKED_BY_ADMINISTRATOR`で拒否されたため、完了していません。
今回は再実行しておらず、この補助テストの成功を確認済みとはしていません。

## 未確認の範囲

WindowsのPython 3.12環境での実行、PowerShell/.NET/PyInstallerによるEXEビルド、
実際のWebView2、マイク入力、Shazamのライブ認識、YouTubeのライブ検索・再生、
MIDI・ホットキー等の実機操作は、この環境では未確認です。

EXE利用時はソースを置き換えるだけでは変更が反映されません。
`build.cmd`で再ビルドしてください。実機では同じ楽曲を本家・カバーの表記で連続認識させ、
履歴と動画選択が繰り返し切り替わらないこと、別曲へ変えると検索が行われること、
動画のループ再生が継続することを確認してください。

4レーン認識の実機確認では、通常時に`Scheduled recognition ... slotInterval=3.0s pool=shared`が出ることを確認してください。WebView2側では`App live PCM bridge armed in WebView2.`の後に`Official page requested app-supplied audio; stream running.`が出ます。4レーンすべてが使用中なら`Recognition slot waiting`が1回出て、workerが空いた後の次の`Scheduled recognition`に`waited=...s`が付いて再開します。旧`Skipped busy/unready`が連続して12秒周期を失う動きは発生しない想定です。

曲認識では1回目に`Candidate pending`、3秒以上ずれて開始した次の有効認識も同一曲なら`Temporal confirmation`が出てから履歴が増えることを確認します。単発の別曲は履歴・YouTube検索・動画切替へ進まないことを確認してください。Shazam.com直マイクと比較し、`Recognition start`から`Recognized`までの時間もログで比較してください。

検索修正の実機確認では、提示された結合文字列を入力し、ログの
`YouTubeSearchThread: query`、`API returned`、`usable video(s)`を確認してください。
初回が0件のときだけ次の検索語へ進むことと、手入力時は自動再生しないことを確認します。

アニメOPモードの実機確認では、自動再生ON→モードONにし、上位4件に対象動画がある曲を自動検索させてください。
該当動画の再生と選択表示、該当なしの1位再生、自動再生OFFでモードがOFF・操作不可になることを確認してください。


### Shazam表示DOMからのアーティスト補完テスト版

Shazamの認識ルートに曲IDとタイトルslugだけが得られ、既存のartistリンク・data-testid・JSON-LDから
アーティストを取得できない場合に、表示中ページから補完する経路を追加しています。
URL slugと画面上のタイトルをNFKC・大小文字・空白・句読点/記号を除去して照合し、
一致したタイトルの直下/近傍にある可視テキストをアーティスト候補として読みます。
`308 · Anime`のような再生/認識数+ジャンル、単独ジャンル、主要UIラベルは候補から除外します。

今回のfixtureでは `/track/436429664/たくさん` に対して、画面が通常の`div/span`だけで
`たくさん!` / `Anastasia (CV: Sumire Uesaka)` / `308 · Anime` と描画された場合に、
`Anastasia (CV: Sumire Uesaka)`を選び、`evidence=route-slug-dom`になることを確認します。
semantic headingだけ存在しartist属性がない場合の`track-heading-nearby`も確認します。

実機確認ではログの `Route evidence` / `Recognized route` の `source` が
`route-slug-dom` または `track-heading-nearby` になり、`artist=Anastasia (CV: Sumire Uesaka)`
が出るか確認してください。WebView2ヘルパーのreadyメッセージ版は`1.2.7-speed1`です。

### Live PCM recognition diagnostics

Live recognition now uses one continuous Web Audio generator backed by a PCM ring buffer rather than scheduling many short AudioBufferSourceNode clips. During a real recognition, stderr should contain:

- `Live PCM reached WebView2 peak=... rms=... queuedMs=...` shortly after the bridge is armed. A peak/RMS near zero indicates the selected PortAudio input is silent or is the wrong device.
- `Live PCM health queuedMs=... pushedMs=... consumedMs=... underflowMs=...` after roughly three seconds. Large `underflowMs` indicates that PCM delivery cannot keep up with real time.
- `Recognized ...` when Shazam returns a usable match.

The temporal two-hit guard and sequence look-ahead run only after a raw Shazam result exists; repeated `source=no-match` therefore points to the audio/WebView recognition path rather than temporal adjudication.


## Live input level diagnostics
A healthy run logs `ShazamService: Audio level rawPeak=... rawRms=... agcGain=... outPeak=... outRms=...`. Very quiet raw input is automatically raised (up to 32x); silence is not boosted. Two-channel endpoints are captured as stereo and downmixed before AGC.

## Shazam確定待機と仮認識の状態分離

追加した独立モデルの検証は次のとおりです。

```powershell
python -m pytest -q tests/test_shazam_state_ownership.py
```

検証対象は、Bの確定済みPRELOAD/PLAYの保護、Cの1回目認識と誤認識取消、
同じYouTube動画IDに対する要求ID照合、Bの再送中のC検索、B再生開始後の
CのPRELOAD解除、Cの2回目一致での正式な所有権移動です。

詳細は [状態管理設計](STATE_MANAGEMENT_REFACTOR.md) を参照してください。


## Shazam検索キュー優先度の回帰確認（2026-10-13）

```powershell
python -m pytest -q tests/test_shazam_search_queue_priority.py
python -m pytest -q tests
node tests/javascript_checks.cjs
node tests/player_recovery_checks.cjs
node tests/player_race_checks.cjs
```

確定済みB検索の保護、Cの1回目認識と取消、Cの2回目一致での
待機中／実行中B検索の取り消し、A検索の遅延、古いB検索結果の遅延配送、
自動再生OFFでの検索保護、通常の手動検索優先をテストしています。
詳しいイベント順序は [SEARCH_QUEUE_PRIORITY.md](SEARCH_QUEUE_PRIORITY.md) を参照してください。


## 2026-10-13 「群青」先行PRELOADの回帰テスト

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_gunjo_preload_regression -v
```

同じ曲IDの後続2回一致で時系列処理が追い越されたときも、旧候補の
YouTube検索とPRELOAD要求IDが引き継がれることを確認します。
対応するログ解析は `docs/GUNJO_PRELOAD_RECOVERY.md` を参照してください。

## YouTube検索0件応答の診断ログ（2026-10-14）

`tests/test_youtube_search_diagnostics.py` にHTTP応答を模擬した診断テストを追加。
HTTP 200でHTMLが返った場合や、正常なJSONの0件、異常JSON、詳細取得不良、
ショート動画・カバー全件除外、通信エラーとクォータ時のAPIキー切替を検査します。
通常成功時のログ行数を増やさず、APIキー・URLクエリ・本文・エラーメッセージを
漏らさないことも検証します。検索・再生動作には変更ありません。
詳細：`YOUTUBE_ZERO_RESULT_DIAGNOSTICS.md`。

### Shazam WebView2 4〜16レーン（設定画面）

- 設定画面 > Shazam > 「WebView2レーン数」は4〜16、初期値は4です。`config.json` では `shazam_lane_count` に保存します。
- モード稼働中の設定保存では、Shazamをいったん停止し、新しいレーン数で再起動します。稼働していない場合は次のShazam開始時に適用します。
- Pythonの `tests/test_shazam_lane_count.py` では既定4、上限16、破損設定の補正、16ワーカーの事前準備、16→4→8への切替、PCMファンアウト、丸め込み、時間差スケジューリング、短時間での停止・再開時の旧世代キュー破棄を確認します。
- Windowsでの確認：起動ログの `ShazamService: Started` に `laneCount=8` など選択値が出ることと、8設定なら `lane-1`〜`lane-8` の `ShazamWebView: helper spawn` が記録されることを確認してください。16から4に下げた後はレーン5以上への新規割当・PREWARMは行われません。
- 全レーン数で新しい認識の開始間隔は3秒以上であり、レーン数を増やしても3秒より短くなりません。レーン数の増加は認識遅延時の同時実行余裕を増やしますが、CPU・メモリ負荷も増えます。
