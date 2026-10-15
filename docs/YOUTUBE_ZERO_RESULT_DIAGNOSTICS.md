# YouTube検索・0件応答の診断ログ（2026-10-14）

## 目的

2026-10-14 の実機ログにおいて、「Naked Story / GARNET CROW」「真昼の空の月」が、複数の検索語でも `API returned 0 item(s)` になりました。現行実装では、HTTP 200 で HTML や壊れた JSON を受け取っても `{}` を返すため、真の0件と異常応答を区別できませんでした。

**今回の変更は診断ログ専用です。** 検索語、フォールバックの回数、動画選択、ショート除外、キー切替、先行検索、PRELOAD、PLAYは変更しません。特定の曲IDや曲名の例外処理も追加していません。既存の HTTP 200/非JSON を `{}` とみなす処理も、今回は維持しています。

## INFOログの出力方針

- **検索成功時：新しい行は0行。** 従来の `query` / `API returned ...` / `usable video(s)` にだけ、6桁の `sid`（検索処理ID）を付加。並行検索中のログを関連付けます。
- **検索APIが0件：既存の `API returned 0 item(s)` 1行を情報付きの1行に変更**し、重複する `0 usable video(s)` の行は省略。追加行なし。
- **APIで結果は得たがフィルター後0件：従来の `0 usable video(s)` の1行へ、理由別の件数を付加。** 「歌ってみた」等の除外が全件であれば、従来の除外説明の重複行は省略。
- **HTTPエラー、通信エラー、動画詳細APIの異常JSON：発生時だけ診断1行を追加。** エラー発生は通常時ではありません。

## 0件診断の項目

| フィールド | 目的 |
| --- | --- |
| `sid`, `attempt`, `keyIndex` | 検索処理・フォールバック番号・使用APIキー**番号のみ**の対応付け |
| `issue` | `api-empty` / `invalid-json` / `api-json-error` / `missing-items` / `unexpected-content-type` / `empty-with-positive-total` など |
| `http`, `ct`, `server` | HTTPステータスとContent-Type、サーバー識別情報 |
| `bytes`, `ms` | 応答本文のバイト数とHTTP取得時間（ミリ秒） |
| `json`, `parseError`, `kind`, `items`, `pageTotal` | JSON解析の成否・APIレスポンス形状・アイテム数・YouTube側の総数情報 |
| `errorCode`, `errorReason` | エラーコードと安全に制限したreason値。**エラーメッセージ本文は非記録** |
| `html`, `redirects`, `host`, `proxyHdr` | HTMLらしい本文・リダイレクト回数・最終ホスト名・中継ヘッダーの存在 |
| `sha12` | 応答本文のSHA-256先頭12桁。複数の0件に同一ブロックページが返ったか判別するための照合用 |
| `apiItems`, `missingVideoId`, `coverExcluded`, `videoIds`, `detailsItems`, `under60s`, `missingDuration` | **APIレスポンス0件ではない**のに、動画の検証・ショート除外で0件になった理由を区別 |

URLのクエリ文字列、APIキー、HTTPレスポンス本文、本文先頭文字列、エラーメッセージ本文はログに出力しません。通信エラーが完全なURLを含んでも、例外名のみを診断に残します。`sid` はアプリ再起動でリセットされる、機密性のない短いランダム識別子です。

## 見分け方

- `issue=api-empty http=200 ct=application/json pageTotal=0`：**JSON上は空の検索結果**。しかしAPI通信経路が本物のYouTube APIだったかは `host`, `server`, `sha12`, ネットワーク環境とあわせて判断します。
- `issue=invalid-json ct=text/html html=1`：HTTP 200でもHTMLが返っており、従来の **偽の0件** になった可能性が高いです。
- `issue=missing-items` / `issue=api-json-error`：APIとして不自然なJSONです。
- `issue=empty-with-positive-total`：`items=[]` と `pageInfo.totalResults>0` が矛盾する、または結果ページが空です。
- `0 usable video(s) ... apiItems=20 under60s=20`：検索APIは20件返し、60秒未満として全件除外しています。
- `0 usable video(s) ... coverExcluded=20`：検索APIは返したが、既存のカバー除外ルールで全件除外。
- `API diagnostic ... stage=videos`：動画詳細の取得が異常。検索結果の有無とは別に確認します。
- `API diagnostic ... issue=transport exception=...`：通信レベルで要求失敗。0件応答ではありません。

注意：`pageInfo.totalResults` は検索条件やAPIの仕様によって概数になるため、この数値だけで障害を断定しません。HTTP 200とJSONパース成功も、企業内プロキシ等による書き換えがないことの保証にはなりません。

## 検証

`tests/test_youtube_search_diagnostics.py` で、正常検索・真の空JSON・200/HTML・異常JSON・非JSON Content-Type・ページ総数不一致・転送・キー切替・通信エラー・ショート動画全件除外・全件カバー除外・同時検索IDを模擬テストしています。実際のYouTubeとの通信はありません。

Windowsでの実機診断はビルド後に `UI: Source build: 2026-10-14-youtube-search-diagnostic-v1` を確認し、次回0件が発生した検索の `sid` に対応する `query` と `API returned 0 item(s)` の**1行**を確認してください。
