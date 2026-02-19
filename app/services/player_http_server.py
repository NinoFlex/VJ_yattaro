"""
HTTPサーバーサービス
YouTubeプレイヤーへのコマンド送信を管理
"""

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import time
import os
import sys
from pathlib import Path


class PlayerCommandHandler(BaseHTTPRequestHandler):
    """プレイヤーコマンド用HTTPリクエストハンドラ"""
    
    # コマンドキュー（スレッド間共有）
    command_queue = []
    queue_lock = threading.Lock()
    # 状態フィードバック用のコールバック
    state_callback = None
    # 静的ファイル配信用（/web 配下）
    web_root = None
    
    def log_message(self, format, *args):
        """ログ出力を抑制"""
        pass
    
    def do_OPTIONS(self):
        """OPTIONSリクエスト処理（CORS対応）"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def do_GET(self):
        """GETリクエスト処理"""
        parsed_path = urlparse(self.path)

        if parsed_path.path == '/poll':
            self.handle_poll()
        elif parsed_path.path == '/status':
            self.handle_status()
        elif parsed_path.path == '/' or parsed_path.path.startswith('/web/') or parsed_path.path.endswith('.html') or parsed_path.path.endswith('.js') or parsed_path.path.endswith('.css'):
            self.handle_static(parsed_path.path)
        else:
            self.send_error(404, "Not Found")

    def handle_static(self, request_path: str):
        """web/ 配下の静的ファイルを配信"""
        try:
            if not self.web_root:
                self.send_error(500, "Web root not configured")
                return

            # '/' は player.html にリダイレクト
            if request_path == '/':
                self.send_response(302)
                self.send_header('Location', '/player.html')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                return

            # '/web/xxx' を 'xxx' に変換、'/player.html' 等も許可
            rel = request_path.lstrip('/')
            if rel.startswith('web/'):
                rel = rel[len('web/'):]

            # パストラバーサル対策
            rel_path = Path(rel)
            if rel_path.is_absolute() or '..' in rel_path.parts:
                self.send_error(400, "Invalid path")
                return

            file_path = (Path(self.web_root) / rel_path).resolve()
            web_root_resolved = Path(self.web_root).resolve()
            if web_root_resolved not in file_path.parents and file_path != web_root_resolved:
                self.send_error(400, "Invalid path")
                return

            if not file_path.exists() or not file_path.is_file():
                self.send_error(404, "Not Found")
                return

            ext = file_path.suffix.lower()
            content_type = 'application/octet-stream'
            if ext == '.html':
                content_type = 'text/html; charset=utf-8'
            elif ext == '.js':
                content_type = 'application/javascript; charset=utf-8'
            elif ext == '.css':
                content_type = 'text/css; charset=utf-8'

            data = file_path.read_bytes()

            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.end_headers()
            self.wfile.write(data)

        except Exception as e:
            print(f"PlayerCommandHandler: Error in static handler: {e}")
            self.send_error(500, "Internal Server Error")
    
    def handle_poll(self):
        """コマンドポーリング処理"""
        try:
            with self.queue_lock:
                if self.command_queue:
                    # キューからコマンドを取得
                    command = self.command_queue.pop(0)
                    response_data = command
                else:
                    # コマンドがない場合は空レスポンス
                    response_data = {"cmd": "", "videoId": ""}
            
            # JSONレスポンスを送信
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.end_headers()
            
            response_json = json.dumps(response_data)
            self.wfile.write(response_json.encode('utf-8'))
            
        except Exception as e:
            print(f"PlayerCommandHandler: Error in poll: {e}")
            self.send_error(500, "Internal Server Error")
    
    def handle_status(self):
        """ステータス確認処理"""
        try:
            with self.queue_lock:
                queue_size = len(self.command_queue)
            
            status_data = {
                "status": "running",
                "queue_size": queue_size,
                "timestamp": time.time()
            }
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response_json = json.dumps(status_data)
            self.wfile.write(response_json.encode('utf-8'))
            
        except Exception as e:
            print(f"PlayerCommandHandler: Error in status: {e}")
            self.send_error(500, "Internal Server Error")
    
    def do_POST(self):
        """POSTリクエスト処理"""
        parsed_path = urlparse(self.path)
        print(f"PlayerCommandHandler: Received POST request for {parsed_path.path}")
        
        if parsed_path.path == '/command':
            self.handle_command()
        elif parsed_path.path == '/feedback':
            self.handle_feedback()
        else:
            print(f"PlayerCommandHandler: Unknown POST path: {parsed_path.path}")
            self.send_error(404, "Not Found")
    
    def handle_command(self):
        """コマンド受信処理"""
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            command_data = json.loads(post_data.decode('utf-8'))
            
            # コマンドをキューに追加
            with self.queue_lock:
                self.command_queue.append(command_data)
            
            print(f"PlayerCommandHandler: Received command: {command_data}")
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = {"status": "success", "message": "Command received"}
            response_json = json.dumps(response)
            self.wfile.write(response_json.encode('utf-8'))
            
        except Exception as e:
            print(f"PlayerCommandHandler: Error in command: {e}")
            self.send_error(500, "Internal Server Error")
    
    def handle_feedback(self):
        """プレイヤーからの状態フィードバック処理"""
        try:
            print(f"PlayerCommandHandler: Starting feedback processing...")
            
            content_length = int(self.headers['Content-Length'])
            print(f"PlayerCommandHandler: Content-Length: {content_length}")
            
            post_data = self.rfile.read(content_length)
            print(f"PlayerCommandHandler: Raw post data: {post_data}")
            
            feedback_data = json.loads(post_data.decode('utf-8'))
            print(f"PlayerCommandHandler: Parsed feedback data: {feedback_data}")
            
            # コールバックがあれば状態フィードバックを通知
            if self.state_callback:
                print(f"PlayerCommandHandler: Calling state callback with feedback data")
                self.state_callback(feedback_data)
            else:
                print(f"PlayerCommandHandler: No state callback available")
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.end_headers()
            
            response = {"status": "success", "message": "Feedback received"}
            response_json = json.dumps(response)
            self.wfile.write(response_json.encode('utf-8'))
            
            print(f"PlayerCommandHandler: Feedback response sent successfully")
            
        except Exception as e:
            print(f"PlayerCommandHandler: Error in feedback: {e}")
            import traceback
            print(f"PlayerCommandHandler: Traceback: {traceback.format_exc()}")
            self.send_error(500, "Internal Server Error")


class PlayerHttpServer:
    """YouTubeプレイヤー用HTTPサーバー"""
    
    def __init__(self, host='127.0.0.1', port=8080):
        self.host = host
        self.port = port
        self.server = None
        self.server_thread = None
        self.is_running = False
        
    def start(self):
        """サーバー起動"""
        if self.is_running:
            print(f"PlayerHttpServer: Server already running on {self.host}:{self.port}")
            return
        
        try:
            # web ルート（exeの場合は実行ファイルと同じ階層のwebフォルダを参照）
            if getattr(sys, 'frozen', False):
                # exe化されている場合
                exe_dir = Path(sys.executable).parent
                web_dir = (exe_dir / 'web').resolve()
            else:
                # 開発環境の場合
                services_dir = Path(__file__).resolve().parent
                project_root = services_dir.parent.parent
                web_dir = (project_root / 'web').resolve()
            
            PlayerCommandHandler.web_root = str(web_dir)

            self.server = HTTPServer((self.host, self.port), PlayerCommandHandler)
            self.is_running = True
            
            # 別スレッドでサーバーを実行
            self.server_thread = threading.Thread(target=self._run_server, daemon=True)
            self.server_thread.start()
            
            print(f"PlayerHttpServer: Server started on http://{self.host}:{self.port}")
            
        except Exception as e:
            print(f"PlayerHttpServer: Failed to start server: {e}")
            self.is_running = False
    
    def _run_server(self):
        """サーバーメインループ"""
        try:
            self.server.serve_forever()
        except Exception as e:
            print(f"PlayerHttpServer: Server error: {e}")
    
    def stop(self):
        """サーバー停止"""
        if not self.is_running:
            return
        
        try:
            if self.server:
                self.server.shutdown()
                self.server.server_close()
            
            if self.server_thread and self.server_thread.is_alive():
                self.server_thread.join(timeout=5)
            
            self.is_running = False
            print("PlayerHttpServer: Server stopped")
            
        except Exception as e:
            print(f"PlayerHttpServer: Error stopping server: {e}")
    
    def set_state_callback(self, callback):
        """状態フィードバック用のコールバックを設定"""
        PlayerCommandHandler.state_callback = callback
        print("PlayerHttpServer: State callback set")
    
    def send_command(self, cmd, video_id=""):
        """コマンドをキューに追加"""
        try:
            command = {
                "cmd": cmd,
                "videoId": video_id,
                "timestamp": time.time()
            }
            
            with PlayerCommandHandler.queue_lock:
                PlayerCommandHandler.command_queue.append(command)
            
            print(f"PlayerHttpServer: Sent command: {cmd} - {video_id}")
            
        except Exception as e:
            print(f"PlayerHttpServer: Error sending command: {e}")
    
    def clear_queue(self):
        """コマンドキューをクリア"""
        try:
            with PlayerCommandHandler.queue_lock:
                PlayerCommandHandler.command_queue.clear()
            print("PlayerHttpServer: Command queue cleared")
            
        except Exception as e:
            print(f"PlayerHttpServer: Error clearing queue: {e}")
    
    def get_queue_size(self):
        """キューのサイズを取得"""
        try:
            with PlayerCommandHandler.queue_lock:
                return len(PlayerCommandHandler.command_queue)
        except Exception as e:
            print(f"PlayerHttpServer: Error getting queue size: {e}")
            return 0


# グローバルインスタンス
player_server = None


def get_player_server():
    """プレイヤーサーバーインスタンスを取得"""
    global player_server
    if player_server is None:
        player_server = PlayerHttpServer()
    return player_server


def start_player_server(host='127.0.0.1', port=8080):
    """プレイヤーサーバーを起動"""
    server = get_player_server()
    server.host = host
    server.port = port
    server.start()
    return server


def stop_player_server():
    """プレイヤーサーバーを停止"""
    global player_server
    if player_server:
        player_server.stop()
        player_server = None
