# テスト手順と今回の確認結果

確認日: 2026-09-08

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
| Pythonの回帰テスト | 79件成功 |
| JavaScriptのモック試験 | 19件成功 |
| Python 3.12の構文としての解析 | Python・specファイル29件成功 |
| JavaScriptの構文検査 | 7ファイル成功 |
| 添付された認識履歴の再現入力 | 5行を2件の連続曲グループへ整理 |
| 同じ履歴を古い順にライブ結果として再生 | 新曲通知2回、履歴更新通知2回 |

入力として添付された個人の履歴JSONは書き換えていません。
そのファイル自体もソースZIPには収録していません。

主な追加テストは、本家とカバーの交互検出、アーティスト相違、3文字一致と4文字一致の境界、
4文字未満の従来判定、同曲が別曲を挟んで再登場する場合、古い認識結果の拒否、
再起動時の初回通知、履歴の読み込みと保存、両レーン使用中の音声変換抑止です。

元のUI、動画プレイヤー、画像、ネイティブヘルパー、ビルド関連の30ファイルは
入力ソースとバイト単位で同一であることを確認しました。
既存のUIハッシュ検査も回帰テストに含まれています。

## 実ブラウザーの補助テスト

実行できる環境でのみ、任意に利用してください。

```powershell
.\.venv\Scripts\python.exe -m pip install playwright
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe tests/browser_checks.py
```

ブラウザー用テストはlocalhost上の模擬ページを使うもので、
実Shazamサイトの認識精度を測るものではありません。
今回の実行環境では`http://localhost:8765/ja-jp`への遷移が
`net::ERR_BLOCKED_BY_ADMINISTRATOR`で拒否されたため、完了していません。
この補助テストの成功を確認済みとはしていません。

## 未確認の範囲

WindowsのPython 3.12環境での実行、PowerShell/.NET/PyInstallerによるEXEビルド、
実際のWebView2、マイク入力、Shazamのライブ認識、YouTubeのライブ検索・再生、
MIDI・ホットキー等の実機操作は、この環境では未確認です。

EXE利用時はソースを置き換えるだけでは変更が反映されません。
`build.cmd`で再ビルドしてください。実機では同じ録音を本家・カバーの表記で連続認識させ、
履歴と動画選択が繰り返し切り替わらないこと、別曲へ変えると検索が行われること、
動画のループ再生が継続することを確認してください。
