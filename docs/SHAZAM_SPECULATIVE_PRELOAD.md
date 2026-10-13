# Shazam 1回目認識からの仮登録・先行YouTube検索・PRELOAD

## 動作（2026-10-13 追加）

1. ShazamがA曲を確定している状態で、1回目のB曲認識結果を受け取ると、Shazam履歴欄の先頭に **`(仮)`** と付けてB曲を一時表示します。
   - `shazam_history.json` にはまだ保存しません。
   - 音声を認識できてもShazam IDのみで曲名がない場合は、Apple等のメタデータ先行取得で有効な曲名が得られてから仮表示・検索を始めます（曲名がないまま検索しません）。
2. 仮登録の時点でYouTube検索を始め、通常は検索結果1位を選んで `PRELOAD` を送信します。アニメOPモードONなら従来の選択順位を使用します。**自動再生OFFでもPRELOADまでは行います。**
3. 同じ曲が独立した2回目のShazam認識で一致した時点で候補を確定します。
   - 自動再生ONかつPRELOADがすでに `ready` なら、即座に `PLAY` を送信します。
   - まだYouTube検索中・PRELOAD中なら、その完了を待って `PLAY` します。
   - 自動再生OFF、または手動再生操作で自動再生を中断している場合は、勝手に `PLAY` しません。
   - 自動再生のON/OFFは**2回目の一致時点**で評価します。仮登録中にOFFにしてもPRELOADと15秒ごとの再送は継続します。2回目一致時にOFFだった曲を、後でONに戻しただけで遡って再生することはありません。
   - 曲名しかない候補でも、自動再生ONなら同じ規則で再生できます。
4. **B曲が誤認識だった場合**は、B曲の仮登録を右のShazam履歴欄から削除します。B曲の検索結果が現在表示中なら左のYouTube検索結果もクリアし、まだ処理中ならその検索結果を無視します。B曲に `PLAY` は送信しません。
   - 別のC曲が次に認識された場合、B曲を除去し、C曲を1回目の候補として新たに先行検索します。
   - `No match` が1～2回続く場合、Shazam ID・15秒以内など従来の中立ギャップ条件を満たす間はB曲の仮登録を維持します。ギャップ上限超過、ハードエラー、15秒経過、Shazam停止時には取り消します。
5. 有効な2回目認識で確定した曲のみ、従来どおりメタデータ確定処理に従って `shazam_history.json` へ保存します。仮登録行は `(仮)` から `(確定・情報補完中)`、正式保存後は通常行へ切り替わります。

既存機能（Shazam音声要求失敗の診断、15秒ごとに最大2回の強制PRELOAD再送、1位が再生不能なら2位にフォールバック）は保持します。仮登録中の1位再生不能も**PLAYせずに**2位をPRELOADするだけです。2回目の一致後に、準備できた動画を自動再生します。

Shazam IDが両方取得できている場合、**異なるIDを持つ2曲は曲名先頭4文字が一致しても別曲**とみなします。IDが使えない曲は従来のタイトル照合に従います。

## 主なログ

```text
ShazamService: Tentative first vote seq=... title='...' artist='...'; start YouTube preload, no PLAY
UI: Shazam tentative row added firstSeq=... title='...' artist='...'; search/preload only
UI: Shazam tentative PRELOAD sent firstSeq=... video=... confirmed=False; awaiting ready
ShazamService: Tentative confirmed firstSeq=... secondSeq=...; playback now permitted
UI: Shazam second vote confirmed firstSeq=... video=... ready=True autoPlay=True
UI: Shazam confirmed; sent PLAY for already-ready cue: ...
ShazamService: Tentative rejected firstSeq=... reason=new-candidate
UI: Shazam tentative removed firstSeq=... reason=...; no history.json change and no PLAY
UI: Ignoring late YouTube results for rejected Shazam candidate ...
```

## Windowsでの手動確認

1. 修正版ソースで `build.cmd` を実行し、Shazamモード・自動再生ONで起動。必要なら出力用ブラウザを開いておきます。
2. A曲を2回認識させて確定・再生開始した後、B曲を流します。B曲の**最初の有効な認識**で右履歴に `(仮)` が追加され、左にYouTube検索結果が出て、ログに `PRELOAD` が送られることを確認します。この段階ではA曲の再生が継続し、B曲は再生されません。
3. B曲の2回目の認識が一致したら、`PLAY` が1回だけ送られることを確認します。検索・プリロードがまだ完了していなければ、`ready` 後に再生されることを確認します。
4. A曲の後に、B曲を**1回だけ**認識させ、続いて別のC曲を認識させてください。B曲の仮登録は消え、C曲の仮登録が表示されることを確認します。B曲が `shazam_history.json` に記録されていないことも確認します。
5. 自動再生OFFで試し、初回に `PRELOAD` されても、2回目一致で `PLAY` されないことを確認します。
   PRELOAD中に自動再生をOFFにしても15秒後の再送が残ること、および2回目一致の後にONへ戻しても勝手にPLAYされないことも確認してください。
6. ShazamがIDのみ返した場合は、メタデータ取得まで仮登録が遅れる場合があります。`No match` も多い環境では、認識開始から **15秒** 以内のギャップ継続と、誤った仮登録削除のログを確認してください。

## 自動テスト

```powershell
python -m unittest discover -s tests -p "test_*.py" -q
node tests/javascript_checks.cjs
node tests/player_recovery_checks.cjs
```

`tests/test_shazam_speculative_preload.py` はShazamの認識順序・誤認識の破棄・履歴未保存、検索／ready／2回目認識の競合、PRELOADフォールバック、無効になった検索結果の無視をオフライン検証します。

**Windows、Rekordbox、実マイク、実Shazamサイト、実YouTubeプレイヤーを使った実機試験は未実施です。** この変更を導入した最初の使用前に、上記の操作試験を実施してください。
