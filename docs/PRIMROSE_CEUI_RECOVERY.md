# Shazam旧形式 `/track/` のアーティスト補完（汎用処理）

## 実行ログの事実

`vj_yattaro(20261013-144526).log` では `/track/65621792/` の識別には成功したが、曲名とアーティストが表示される前にWebView2が曲IDだけを返した。
その後、HTTP取得が無関係な `/song/1787023936/dtmf` にリダイレクトされたため、アプリは安全のためそのページを拒否した。曲名「風のなかのプリムローズ」はURLから補完されたが、アーティスト名は空欄だった。
一方、ユーザー保存のShazam一次ページには同じ曲IDの専用ヘッダーとして「Ceui」がある。

## 汎用実装（曲ID・アーティスト名の固定登録はしない）

1. **WebView2:** 旧形式 `/track/` の情報が未描画の場合、IDのみで早期返却するまでの猶予を約3秒に延長する。タイトル・アーティスト名を取得できれば即座に返す。通常の `/song/` は従来の約900msを維持。
2. **Shazamページ取得:** 曲IDに基づき `/ja-jp/track/<id>/unavailable`、認識時のURL、`/track/<id>/unavailable` の順に試す（不必要な問い合わせや情報の混入を防ぐため完了次第終了）。
3. **検証:** 取得したHTMLのcanonical/og:urlで同一Shazam曲ID、同一 `/track/` または `/song/` 名前空間を検証し、当該曲専用ヘッダーに存在するタイトル・アーティストのみ採用する。無関係なリダイレクト、関連曲カード、広告文言は拒否。
4. **正常経路の維持:** WebView2でタイトルとアーティスト名が揃った曲は追加HTTP取得せず、従来どおり先行YouTube検索へ進む。仮→確定・PRELOAD・PLAYの制御は変更しない。

**明示的に撤回:** 旧版にあった `app/services/shazam_verified_tracks.py` と、曲ID `65621792` の場合だけ `Ceui` を代入する分岐を削除。曲固有の例外・定数テーブルは製品コードに置かない。

## 実機での制限

保存HTMLから曲名・アーティストを抽出できることはオフラインで確認できる。しかし実機のHTTPアクセスが同一IDのページを取得できず、WebView2の3秒猶予でもDOMを取得できなければ、**アーティスト名は引き続き空欄になり得る**。勝手に推測したり固有IDで補完したりしない。

`tests/test_shazam_primrose_recovery.py` では、実例だけでなく任意の別ID・別アーティストにも同一コードが適用されること、リダイレクト時に空欄のままとすること、正常メタデータを上書きしないことを検証する。

次のログ識別子を確認する:

```text
UI: Source build: 2026-10-14-shazam-artist-generic-v3
```

正しい同一曲のページに到達した場合は `ShazamMetadata: exact primary Shazam page artist accepted`、元から揃っている曲は `ShazamService: Using complete live Shazam metadata without Apple lookup` が記録される。通常のWindows実機でのWebView2とHTTP応答は別途検証が必要。
