# Shazam曲名だけの認識からアーティスト名を補完（2026-10-13）

## 現場ログと保存ページ

対象ログ: `vj_yattaro(20261013-134803).log`

- 22:46:54: `shazamTrackId=228936711`, `source=shazam-route`、曲名・アーティストとも空で初期返却。
- 22:46:56: `/track/228936711` 由来の曲名は `Ray Of Bullet` に解決できたが、アーティストは空欄。
- 一般ページの補完では `https://www.shazam.com/song/1787023936/dtmf` という**別の曲ID**へ転送され、`identityVerified=False` として正しく拒否された。
- ユーザー保存の `https://www.shazam.com/ja-jp/track/228936711/unavailable` には同じ曲IDのcanonicalがあり、専用ヘッダーに `Ray Of Bullet` とアーティストが存在する。
- 当該保存ページのアーティスト名は「銃皇無尽のファフニー & リス・フレイア、物部深月(声優:沼倉 (声優:日高里菜)」。文字列自体の表記が不自然でもアプリ独自で推測修正しない。

## 修正内容

`app/services/itunes_metadata.py`:

1. 通常の`og:title`だけでなく、CSSハッシュに依存しない
   `NewTrackPageHeader_trackTitle__*` と `TrackPageArtistLink_artistNameText__*` の
   **同一曲専用の表示領域**を解析する。
2. `/track/{id}/...` を取得する場合、保存ページと同じ
   `/ja-jp/track/{id}/unavailable` を優先する。情報が取れなければ元のURLを再試行する。
3. Shazamのcanonicalと`og:url`の宣言が**どちらも同じ曲IDかつ同じ`track`/`song`名前空間**である場合だけ採用。
   別曲リダイレクト、他曲の推薦表示、一般のApple Music宣伝文言は採用しない。
4. 曲名が既知でアーティストだけ空のケースでは、補完成功時に
   曲名とアーティストを合わせて仮候補・YouTube検索に渡す。
5. 情報の確認に失敗した場合は従来どおり曲名のみの検索・再生を維持。

## 検証と制限

`tests/test_shazam_primary_page_artist.py` を追加。保存ページから最小限の構造を抽出した
`tests/fixtures/shazam_structural/ray_of_bullet_primary_header.html` を用い、
曲ID一致、異なるID/名前空間の拒否、複数候補の拒否、宣伝文言の除外、
通常URLへのフォールバック、再取得のキャッシュを試験。

全テストはローカルモックによるもので、**実際のWindows WebView2環境や
Shazamサイトへ接続したHTTP応答は未検証**。特に保存されたHTMLは
ブラウザ上で描画済みのため、一般のHTTP GETが同じ表示済みHTMLを返すとは限らない。
新しいEXEで `ShazamMetadata: exact primary Shazam page artist accepted`
が出るか、曲名だけのフォールバックになるかをログで確認する。

ログの起動識別子は `UI: Source build: 2026-10-13-shazam-track-artist-v1`。
