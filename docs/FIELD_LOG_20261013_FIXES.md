# 2026-10-13 現場ログからのShazam仮登録・自動検索修正

対象ログ: `vj_yattaro(20261013-121157).log`。ログ自体はこの配布物に含めません。
ベース: `VJ_yattaro_search_queue_priority_git_ready.zip`。

## 1. 確定済み新曲のYouTube検索が抜ける

**観測:** 21:10:19.912 にID `6785720953`（後で「月に願う」）が2回一致。
21:10:20.721 に曲名が補完されたにもかかわらず、21:10:20.725 のUIログは
`Shazam tentative already searched firstSeq=63` と表示。`firstSeq=63` は前曲です。

**根本原因:** `main.py` の履歴更新/新曲通知が、曲の対応を照合せずに
`_shazam_preview.confirmed / committed` を見て検索済みと判断していた。
加えて、IDだけのShazam結果はメタデータ補完が「2回一致の後」になった場合に
仮登録通知を発行できず、先行検索へ入れなかった。

**修正:** Shazamサービスから履歴更新/新曲通知を発行する直前に、その公開内容と
一致する候補ID `_last_publication_candidate_id` を設定。UIはそのIDが一致する場合のみ
先行検索済みと判定。実際の候補IDを取得できないテスト/旧経路では
曲名・アーティストの厳密一致で判断。
2回一致の後にIDメタデータが届いた場合も、候補開始→候補確定の順に通知し、
検索/PRELOAD/PLAYが従来の候補所有権モデルで動作する。
既に別曲の認識で無効になった古いメタデータは、遅延到着しても再生しない。

## 2. 2回一致が速く、仮登録を視認できない場合（仕様どおり）

**観測:** 例として 21:10:02.628 に firstSeq=63 の仮登録、
21:10:02.630 に2回一致（約2ms）。Qtの描画更新より先に確定した場合、
画面上の `(仮)` は見えないことがある。

**判断:** この挙動は不具合ではないため、いったん追加した最低450msの
仮表示維持と470msの再描画タイマーは撤回した。1回目には `(仮)` を
モデルに追加するが、2回一致した時点ですぐ確定表示に更新する。
YouTube検索・PRELOAD・PLAYは従来どおり遅延させない。

## 3. 同じ曲なのに仮候補が消え、再登録される

**観測例1:** 21:07:05.426 の「Os-宇宙人」firstSeq=24 を、
21:07:05.499 の temporal head bypass (seq26/27) が
`older-observation-bypassed` として削除し、firstSeq=26 として新規仮登録。
**同じShazam曲ID**なのに候補が別物になっていた。

**観測例2:** 21:05:34.494 の「Rumbling hearts」firstSeq=8 は、
後続の同曲認識がseq順序待ちで滞留し、21:05:49.602 に仮表示期限切れで削除。
21:05:51.395 に同曲の後続 seq12/13 が追い越し確定された。

**修正:** temporal head bypassの先頭が現在の仮候補と同じ楽曲IDの場合、
同一候補IDを保持し、新しい合法な隣接ペアで2回一致を確定する。
古い候補に重複する検索・PRELOADは発生させない。
既に確定済みで曲名補完待ちの候補も同一IDなら引き継ぎ、確定通知は重複させない。
異なる曲IDの場合は従来どおり候補を削除する。

15秒の仮登録期限については、最初の認識の後に**同じ曲の独立した認識結果が
既に戻っているが、より古いseqの完了待ち**である場合のみ、
その新しい観測時刻を使って仮表示を延長する。無制限には延長せず、
最初の観測から最大30秒で失効する。未完了の認識や欠番は
2回一致の証拠として扱わない。単発の誤認識は従来どおり約15秒で取り消す。

## テスト

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -q
.\.venv\Scripts\python.exe -m unittest tests.test_shazam_field_log_regressions -v
node tests/javascript_checks.cjs
node tests/player_recovery_checks.cjs
node tests/player_race_checks.cjs
```

追加 `tests/test_shazam_field_log_regressions.py` は11件。
仮表示テストは最低表示時間の保証ではなく、確定時の即時更新を確認する。
Pythonは実際のブラウザやQt描画ではなく、時系列・通知・UIデータをモックで検証。
Windows EXEビルド、実マイク、Shazam/YouTube実通信および実際の画面描画は未検証。

実機では、同じ曲の2票がほぼ同時に返ること、ID-onlyが2票先行すること、
4レーンの欠番や時間超過があること、曲切替時に不要な検索が走らないことを確認する。

## 別件：起動時のデバイスエラー

同じログの21:04:25にはPortAudio `Invalid device [PaErrorCode -9996]` がある。
21:04:57にWASAPIの有効なデバイスを選択し直してShazamが開始している。
これは今回の候補/検索ロジック問題と独立した入力デバイス設定の問題。
