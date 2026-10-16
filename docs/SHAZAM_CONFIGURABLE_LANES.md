# Shazam WebView2 レーン数（4〜16）の設定

## 利用方法

設定画面 > **Shazam** > **WebView2レーン数** から4〜16を選んで保存します。初期値は4です。
`config.json` のキーは `shazam_lane_count` で、不正値や範囲外の値は起動時に4〜16へ補正されます。
Shazam稼働中の設定変更ではいったん認識・音声取り込みを停止し、新しい数のワーカーで再起動します。

## 実装

- 各レーンは独立したWebView2ヘルパーと専用キュー・ワーカー・事前準備完了イベントを所有します。最大16レーンまで動的に拡張します。
- スケジューラは設定したレーンのみround-robinで選び、全レーンbusyの場合はスロットを保持します。通常の認識開始間隔は全体で3秒固定です。
- 設定を減らす場合、`stop()` で全ヘルパーのWebView2プロセスを停止します。新しい設定で有効なレーンだけPREWARMします。古いレーンのワーカースレッド枠はプロセス終了まで待機状態として残る場合がありますが、ブラウザは起動せず、認識も割り当てません（遅延した旧世代の結果を安全に拒否するため）。
- 再開時は旧世代のキュー上の処理要求を破棄し、新世代のPREWARMだけを登録するため、準備完了の誤判定を回避します。
- 4レーンの確認窓・2回一致条件・Shazamページ解析・YouTube検索・PRELOAD／PLAYのロジックは変更していません。

## 注意

レーン数を増やしても3秒ごとの開始間隔自体は変わりません。多くのWebView2プロセスが並行稼働するため、CPU・メモリ使用量、Shazam側の処理負荷は増加します。推奨値は従来の4から開始し、busy待ちが頻発する場合に増やすことです。

## テスト

- `python -m unittest tests.test_shazam_lane_count -v`
- `python -m unittest discover -s tests -p 'test_*.py' -q`
- `node tests/javascript_checks.cjs`
- `node tests/player_recovery_checks.cjs`
- `node tests/player_race_checks.cjs`

Windows実機における最大16レーンのWebView2起動／メモリ使用量／Shazamサイト挙動はオフライン環境では検証できないため、配布後に動作ログで確認します。
