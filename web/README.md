# VJ YouTube Player

YouTube IFrame Player APIを使用したA/B 2プレイヤーによるプリロード+滑らかな切り替えを実現するWebプレイヤーです。

## 機能

- **A/B 2プレイヤー方式**: 常に2つのプレイヤーを保持し、片方が再生中に次の動画をプリロード
- **滑らかな切り替え**: フェードトランジションによるシームレスな動画切り替え
- **自動ループ再生**: 動画終了時に自動的にループ再生
- **ミュート固定**: VJ用途を想定し、常にミュート状態で再生
- **HTTPサーバー統合**: Pythonアプリケーションから自動起動・制御
- **状態フィードバック**: プレイヤーの状態をリアルタイムでアプリケーションに通知
- **自動ブラウザ起動**: アプリケーション起動時にプレイヤーを自動で開く

## 使用方法

### 自動起動（推奨）
VJ_yattaroアプリケーションを起動すると、自動的に以下のURLでプレイヤーが開きます：
```
http://localhost:8080/player.html?defaultVideoId=eyUUHfVm8Ik
```

### 手動起動
1. HTTPサーバーを起動（ポート8080）
2. ブラウザで `player.html` を開く
3. F11キーで全画面表示

### 起動時の動作

- **自動再生**: 起動後2秒でデフォルト動画（eyUUHfVm8Ik）を自動再生
- **状態リセット**: 起動時にすべての状態をリセットし、枠の色をデフォルト（茶色 #a52a2a）に設定
- **ループ再生**: デフォルト動画も自動でループ再生
- **ブラウザ自動起動**: アプリケーション起動時に既定ブラウザでプレイヤーを開く

## API仕様

### エンドポイント

#### コマンドポーリング
```
GET http://localhost:8080/poll
```

#### 状態フィードバック
```
POST http://localhost:8080/feedback
```

#### ステータス確認
```
GET http://localhost:8080/status
```

#### 静的ファイル配信
```
GET http://localhost:8080/player.html
GET http://localhost:8080/player.js
GET http://localhost:8080/player.css
```

### コマンド仕様

#### PRELOAD
次の動画をプリロード開始します。
```json
{
  "cmd": "PRELOAD",
  "videoId": "dQw4w9WgXcQ"
}
```

#### PLAY
指定された動画を再生開始します。
```json
{
  "cmd": "PLAY",
  "videoId": "dQw4w9WgXcQ"
}
```

### 状態フィードバック

プレイヤーからアプリケーションへの状態通知：

```json
{
  "state": "preloading|ready|playing|ended|error",
  "videoId": "YouTube動画ID",
  "timestamp": 1234567890123
}
```

#### 状態の種類
- **preloading**: 動画のプリロード中
- **ready**: プリロード完了、再生準備完了
- **playing**: 再生中
- **ended**: 再生終了
- **error**: エラー発生

## 技術仕様

### プレイヤー設定
- ミュート固定
- 自動再生ON
- ループ再生ON
- コントロール非表示
- 100vw × 100vh全画面表示

### HTTPサーバー
- デフォルトポート: 8080（設定ファイルで変更可能）
- ポーリング間隔: 100ms
- 自動再接続機能
- CORS対応
- 静的ファイル配信（/web 配下）

### トランジション
- フェード時間: 0.3秒
- Z-indexによるプレイヤー重ね付け
- CSSトランジション使用

### 統合機能
- **自動ブラウザ起動**: アプリ起動時に `webbrowser.open()` でプレイヤーを開く
- **デフォルト動画ID**: eyUUHfVm8Ik をクエリパラメータで渡す
- **状態同期**: プレイヤーの状態変更をアプリケーションにリアルタイム通知
- **コマンドキュー**: スレッドセーフなコマンド送信

## ファイル構成

```
web/
├── player.html      # メインHTMLファイル
├── player.css       # スタイルシート
├── player.js        # プレイヤーロジック
└── README.md        # このファイル

app/services/
└── player_http_server.py  # HTTPサーバーサービス
```

## ブラウザ対応

- Chrome/Chromium
- Firefox
- Safari
- Edge

## 設定

### config.jsonでの設定項目
```json
{
  "player_port": 8080,
  "default_video_id": "eyUUHfVm8Ik"
}
```

## 注意事項

- YouTube IFrame APIの読み込みが必要
- 全画面表示を推奨（F11）
- HTTPサーバー経由でのアクセスが必要
- 動画は常にミュートで再生
- アプリケーションと連携するため、単体での使用は推奨されない

## VJ_yattaroとの連携

このプレイヤーはVJ_yattaroアプリケーションの一部として動作します：

1. **起動**: アプリ起動時に自動でプレイヤーを開く
2. **検索連携**: YouTube検索結果から直接動画を再生
3. **ホットキー操作**: キーボードショートカットで動画選択・再生
4. **状態表示**: アプリ側で現在の再生状態を枠の色で表示
5. **履歴連携**: Rekordboxの再生履歴からYouTube検索
