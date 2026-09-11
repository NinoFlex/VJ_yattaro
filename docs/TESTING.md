# テスト手順と今回の確認結果

確認日: 2026-09-10

## オフライン回帰テスト

Windows用の開発環境では、ルートフォルダーで実行します。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
node tests/javascript_checks.cjs
```

PythonのテストはQtと通信をモック化しています。実機の認識試験ではありません。
`test_track_matching.py`自体は標準ライブラリーだけで実行でき、
既存の統合テストには`numpy`と`requests`も必要です。

今回、Linux / Python 3.13.5 / Node.js 22.16.0で次を確認しました。

| 確認対象 | 結果 |
|---|---|
| Pythonの回帰テスト | 182件成功 |
| JavaScriptのモック試験 | 21件成功 |
| Python 3.12の構文としての解析 | Python・specファイル31件成功 |
| JavaScriptの構文検査 | 7ファイル成功 |

入力として添付された個人の履歴JSONは書き換えていません。
そのファイル自体もソースZIPには収録していません。

主な既存テストは、本家とカバーの交互検出、アーティスト相違、3文字一致と4文字一致の境界、
4文字未満の従来判定、同曲が別曲を挟んで再登場する場合、古い認識結果の拒否、
再起動時の初回通知、履歴の読み込みと保存、全グループ使用中の音声変換抑止です。

### Shazam 4レーン共有プール・時系列確認の追加確認

`ShazamService`をQt/WebView2だけ模擬して確認しています。

- 4レーン・3秒間隔の定数を維持すること。
- 1回目の有効結果だけでは履歴を更新せず`Candidate pending`になること。
- 次の有効結果が既存のタイトル前方一致ルールで同一曲なら確定すること。
- 異なるタイトルが1回だけ入っても確定されないこと。
- `A → X → A`で単発の`X`を履歴追加しないこと。
- 認識完了順が前後しても要求連番`seq`順で判定すること。
- round-robin上の優先レーンがbusyでも、別の空きレーンへ認識を投入すること。
- 4レーンすべてbusyの場合は要求連番とスロットを消費せず待機すること。
- 待機中にworkerが空けば、そのworkerへ最新スナップショットを投入して再開すること。
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

Shazam履歴のアーティスト名が空の場合は、YouTube検索と候補表示は行いますが、
自動再生フラグを強制的にOFFにすることも確認しています。Rekordbox側のタイトルのみ検索は
この制限の対象外です。

### アニメOPモードの追加確認

`test_autoplay_selection.py`に45件を追加しました。このファイルは標準ライブラリーだけで実行できます。

- 上位4件の判定、1:30・1:45を含む境界、5位以下の除外、複数該当時の順位優先。
- 長さ不明・不正値・空の結果、該当なしなら1位に戻ること。
- 自動再生OFF時のモード解除・操作禁止・保存値の修正、再起動時の復元。
- 手動検索での自動再生抑止、優先動画へのPRELOAD→ready→PLAY、選択表示の一致。

選曲関数と`main.py`の実際のメソッドを実行し、Qtボタン・リスト・設定保存・プレイヤーは模擬しています。
この環境ではPySide6を導入できなかったため、実画面の表示・クリック操作は未確認です。
今回のbusy改善では`app/services/shazam_service.py`、時系列/共有プールのテスト、説明文を変更しています。
offset判定は廃止し、ネイティブWebView2ブリッジもoffsetを返さない単純な経路へ戻しています。`web/`、`ui/`、YouTube検索、アニメOP選曲の実装は変更していません。

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
`build.cmd`で再ビルドしてください。実機では同じ録音を本家・カバーの表記で連続認識させ、
履歴と動画選択が繰り返し切り替わらないこと、別曲へ変えると検索が行われること、
動画のループ再生が継続することを確認してください。

4レーン認識の実機確認では、通常時に`Scheduled recognition ... slotInterval=3.0s pool=shared`が出ることを確認してください。4レーンすべてが使用中なら`Recognition slot waiting`が1回出て、workerが空いた後の次の`Scheduled recognition`に`waited=...s`が付いて再開します。旧`Skipped busy/unready`が連続して12秒周期を失う動きは発生しない想定です。

曲認識では1回目に`Candidate pending`、次の有効認識も同一曲なら`Temporal confirmation`が出てから履歴が増えることを確認します。単発の別曲は履歴・YouTube検索・動画切替へ進まないことを確認してください。

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
