# Shazam先行検索: 確定済み検索Bの優先保護（2026-10-13）

## 修正内容

YouTube検索スレッドがすでに別の楽曲Aを検索中の場合、従来は保留中の検索を最新1件で上書きしていました。そのため、B曲が2回一致して確定済みであっても、C曲の**初回認識**でB曲の検索が消える問題がありました。

今回、`app/models/shazam_state.py` の検索状態を、次の2枠に分離しました。

- `queued_search`: 次に必ず実行すべき検索。ここに**確定済みB曲**がある場合、C曲の初回認識では上書きしません。
- `speculative_search`: B曲の後に検索する最新の**未確定C曲**。C曲が取り消された場合、この枠だけを破棄します。

`SearchRequest.confirmed` はShazamの2回一致時に設定します。自動再生のON/OFFとは独立した優先度です。`confirmed_search_owner` により、再生OFFでも確定B曲の検索結果を正しく受け取れます。従来の `main.py::_pending_search_args` は型付きキューの互換用ミラーにしており、独立に上書きされません。

## 処理順序

| イベント | 次に実行する検索 | 低優先度の保留 | B検索の扱い |
|---|---|---|---|
| A検索中→Bを1回認識 | B | なし | Bは仮登録 |
| A検索中→Bを2回一致 | **B（確定）** | なし | B優先度を確定 |
| A検索中→Cを1回認識 | **B（確定）** | C（仮） | Bを維持 |
| Cが不一致／期限切れ | **B（確定）** | なし | Bを維持 |
| A検索終了→B検索開始 | C（仮） | なし | Bを実行、Cは次を待つ |
| B検索終了→Cがまだ仮 | C（仮） | なし | B結果を反映後、C検索を開始 |
| **Cが2回一致（Bは未開始）** | **C（確定）** | なし | Bを取り消しCへ交代 |
| **Cが2回一致（B実行中）** | **C（確定）** | なし | Bの結果を無効化し、Bスレッド終了後Cを検索 |

- Bの検索が先に完了しても、Cが未確定である間はBの検索結果を捨てません。BのPRELOAD/PLAYは従来の確定曲保護に従います。
- B検索開始後にCの仮候補が取り消された場合は、Cだけをキャンセルします。
- **Cの2回一致後でも、進行中のQThreadを強制終了はしません。** 古いB結果は無効として無視し、スレッドの正常終了後にCを実行します。
- スレッド終了と`finished`シグナルの到着が前後した場合も、未実行のB検索よりC検索を先に開始しません。
- 検索結果・エラー・`finished`通知は送信元QThreadを照合し、古いB通知を新しいCのものとして扱いません。
- 手動YouTube検索は明示的な上書きとして扱い、従来の「新しい手動検索を優先する」動作を維持します。

## 修正したファイル

- `app/models/search_request.py`: 検索元・確定状態を検索要求に保持
- `app/models/shazam_state.py`: 確定済み検索と仮候補検索の別管理、確認・取消・取り出し処理
- `main.py`: 検索キューとの同期、確定／取消時の同期、要求順での検索実行、遅延Qtシグナルの照合
- `tests/test_shazam_search_queue_priority.py`: 低速検索を模したオフライン再現テスト

## テスト方法

```powershell
python -m pytest -q tests/test_shazam_search_queue_priority.py
python -m pytest -q tests
node tests/javascript_checks.cjs
node tests/player_recovery_checks.cjs
node tests/player_race_checks.cjs
```

実機の確認では、A曲のYouTube検索中にBを2回一致させ、Cを1回だけ認識して取り消します。`Search queued (speculative): Song C`、`Executing pending search: Song B confirmed=True` の順に現れ、Bの検索結果が反映されることを確認します。次に同じ条件でCを2回一致させ、`Executing pending search: Song C confirmed=True` となることを確認してください。

## 未検証の範囲

Windows / PySide6 の実QThread配送、実YouTube APIの低速応答、実Shazam/WebView2での認識、EXEビルドはこの環境では未検証です。今回の変更は検索優先度の修正であり、YouTube APIの通信タイムアウトやレート制限の再試行回数は変更していません。
