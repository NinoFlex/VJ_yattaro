# Build hotfix v1.1.1

`tools/build.ps1` の Python 3.12 / 64-bit 判定で、PowerShell のネイティブ引数処理により `struct.calcsize("P")` の引用符が失われ、`NameError: name 'P' is not defined` になる問題を修正しました。

修正版では Python `-c` の判定式から引用符依存を除去しています。

既存の `.venv` は削除不要です。ルートで `build.cmd` を再実行してください。
