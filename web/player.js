// VJ YouTube Player - A/B 2プレイヤーによるプリロード+滑らかな切り替え

class VJPlayer {
    constructor() {
        this.players = {};
        this.currentPlayer = 'A';
        this.nextPlayer = 'B';
        this.currentVideoId = null;
        this.nextVideoId = null;
        this.isReady = { A: false, B: false };
        this.pollingInterval = null;
        // 接続先ポートは、読み込んでいるページ（player.html）のポートに追従
        // 例: http://127.0.0.1:8080/player.html → 8080
        const pagePort = window.location.port ? parseInt(window.location.port, 10) : 80;
        this.pollingPort = Number.isFinite(pagePort) ? pagePort : 8080;
        this.pollingUrl = `${window.location.protocol}//${window.location.hostname}:${this.pollingPort}/poll`;
        this.feedbackUrl = `${window.location.protocol}//${window.location.hostname}:${this.pollingPort}/feedback`;
        
        // デフォルト動画（起動時に自動再生）
        // player側で固定値を持たず、ツール側が player.html のクエリで渡す
        // 例: http://127.0.0.1:8080/player.html?defaultVideoId=xxxxxxxxxxx
        this.defaultVideoId = this.getDefaultVideoIdFromQuery();

        // エラーループ抑止
        this._fallbackAttempts = { A: 0, B: 0 };
        this._lastErrorAtMs = { A: 0, B: 0 };
        this._errorBurstCount = { A: 0, B: 0 };
        this._lastErrorCode = { A: null, B: null };
        this._lastPlayerState = { A: null, B: null };
        this._lastPlayingAtMs = { A: 0, B: 0 };
        
        console.log('VJ Player initialized');
        this.init();
    }

    init() {
        // YouTube APIが既に読み込まれているかチェック
        if (typeof YT !== 'undefined' && typeof YT.Player !== 'undefined') {
            console.log('YouTube API already loaded');
            this.createPlayers();
            this.startPolling();
        } else {
            console.log('Waiting for YouTube API...');
            window.onYouTubeIframeAPIReady = () => {
                console.log('YouTube IFrame API ready');
                this.createPlayers();
                this.startPolling();
            };
        }
    }

    createPlayers() {
        console.log('Creating players...');
        console.log('YT.Player available:', typeof YT.Player);
        
        try {
            // プレイヤーAの作成
            this.players.A = new YT.Player('playerA', {
                height: '100vh',
                width: '100vw',
                playerVars: {
                    autoplay: 0,
                    controls: 0,
                    mute: 1,
                    playsinline: 1,
                    rel: 0,           // 関連動画を非表示
                    showinfo: 0,       // 動画情報を非表示
                    modestbranding: 1,  // YouTubeロゴを最小化
                    iv_load_policy: 3,  // アノテーションを非表示
                    cc_load_policy: 0,  // 字幕を非表示
                    fs: 0            // 全画面ボタンを非表示
                },
                events: {
                    onReady: (event) => this.onPlayerReady('A', event),
                    onStateChange: (event) => this.onPlayerStateChange('A', event),
                    onError: (event) => this.onPlayerError('A', event)
                }
            });

            // プレイヤーBの作成
            this.players.B = new YT.Player('playerB', {
                height: '100vh',
                width: '100vw',
                playerVars: {
                    autoplay: 0,
                    controls: 0,
                    mute: 1,
                    playsinline: 1,
                    rel: 0,           // 関連動画を非表示
                    showinfo: 0,       // 動画情報を非表示
                    modestbranding: 1,  // YouTubeロゴを最小化
                    iv_load_policy: 3,  // アノテーションを非表示
                    cc_load_policy: 0,  // 字幕を非表示
                    fs: 0            // 全画面ボタンを非表示
                },
                events: {
                    onReady: (event) => this.onPlayerReady('B', event),
                    onStateChange: (event) => this.onPlayerStateChange('B', event),
                    onError: (event) => this.onPlayerError('B', event)
                }
            });

            console.log('Players created');
            console.log('Player A created:', !!this.players.A);
            console.log('Player B created:', !!this.players.B);
        } catch (error) {
            console.error('Error creating players:', error);
            // プレイヤー作成失敗時は手動再生案内を表示
            this.showManualPlayback();
        }
    }

    showManualPlayback() {
        console.log('Showing error notification');
        this.showErrorNotification('この動画は再生できません');
    }

    showErrorNotification(message) {
        // 既存の通知があれば削除
        const existing = document.querySelector('.error-notification');
        if (existing) {
            existing.remove();
        }

        // 通知要素を作成
        const notification = document.createElement('div');
        notification.className = 'error-notification';
        notification.textContent = message;
        
        // スタイルを設定
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: rgba(220, 53, 69, 0.9);
            color: white;
            padding: 12px 20px;
            border-radius: 6px;
            font-size: 14px;
            z-index: 10000;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            animation: slideIn 0.3s ease-out;
        `;
        
        // アニメーションを追加
        const style = document.createElement('style');
        style.textContent = `
            @keyframes slideIn {
                from { transform: translateX(100%); opacity: 0; }
                to { transform: translateX(0); opacity: 1; }
            }
            @keyframes slideOut {
                from { transform: translateX(0); opacity: 1; }
                to { transform: translateX(100%); opacity: 0; }
            }
        `;
        document.head.appendChild(style);
        
        document.body.appendChild(notification);
        
        // 3秒後に自動で消す
        setTimeout(() => {
            notification.style.animation = 'slideOut 0.3s ease-out';
            setTimeout(() => {
                if (notification.parentNode) {
                    notification.remove();
                }
            }, 300);
        }, 3000);
    }

    getDefaultVideoIdFromQuery() {
        try {
            const params = new URLSearchParams(window.location.search);
            const id = params.get('defaultVideoId');
            return id && id.trim() ? id.trim() : null;
        } catch (e) {
            console.warn('Failed to parse defaultVideoId from query:', e);
            return null;
        }
    }

    onPlayerReady(playerId, event) {
        console.log(`Player ${playerId} is ready`);
        this.isReady[playerId] = true;
        
        // 両方のプレイヤーが準備完了したら
        if (this.isReady.A && this.isReady.B) {
            console.log('Both players are ready');
            this.setupInitialStates();
        }
    }

    setupInitialStates() {
        // プレイヤーAを前面に、プレイヤーBを背面に配置
        document.getElementById('playerAContainer').classList.add('active');
        document.getElementById('playerBContainer').classList.add('hidden');
        
        // デフォルト動画を自動再生（クエリで渡された場合のみ）
        if (this.defaultVideoId) {
            console.log('Starting default video playback');
            setTimeout(() => {
                this.playDefaultVideo();
            }, 2000); // 2秒後に開始
        } else {
            console.log('No defaultVideoId provided. Skipping auto-play.');
            this.showManualPlayback();
        }
    }
    
    playDefaultVideo() {
        // デフォルト動画を再生
        console.log(`Playing default video: ${this.defaultVideoId}`);
        
        try {
            this.players.A.loadVideoById({
                videoId: this.defaultVideoId,
                startSeconds: 0,
                suggestedQuality: 'medium' // 品質を下げて安定性を向上
            });
            
            // 状態を更新
            this.currentVideoId = this.defaultVideoId;
            this.currentPlayer = 'A';
            
            // 状態フィードバックを送信
            this.sendFeedback('playing', this.defaultVideoId);
            
            console.log(`Default video started: ${this.defaultVideoId}`);
        } catch (error) {
            console.error('Error playing default video:', error);
            // 再生失敗時は手動再生案内を表示
            this.showManualPlayback();
        }
    }

    onPlayerStateChange(playerId, event) {
        const state = event.data;
        this._lastPlayerState[playerId] = state;
        if (state === YT.PlayerState.PLAYING) {
            this._lastPlayingAtMs[playerId] = Date.now();
        }
        console.log(`Player ${playerId} state changed: ${state}`);
        
        // isReady状態の更新
        if (state === YT.PlayerState.CUED || state === YT.PlayerState.PLAYING) {
            this.isReady[playerId] = true;
            console.log(`Player ${playerId} is now ready (state: ${state})`);
        } else if (state === YT.PlayerState.UNSTARTED || state === YT.PlayerState.BUFFERING) {
            this.isReady[playerId] = false;
            console.log(`Player ${playerId} not ready (state: ${state})`);
        }
        
        // 動画終了時の処理（ループ）
        if (state === YT.PlayerState.ENDED && playerId === this.currentPlayer) {
            this.players[playerId].playVideo();
        }
    }

    onPlayerError(playerId, event) {
        console.error(`Player ${playerId} error:`, event);
        console.error(`Error code: ${event.data}`);
        
        // エラーコードの詳細
        const errorCodes = {
            2: 'Invalid parameter',
            5: 'HTML5 player error',
            100: 'Video not found or removed',
            101: 'Video not embeddable',
            150: 'HTML5 player error',
            153: 'HTML5 player error - video may not be embeddable or has restrictions'
        };
        
        console.error(`Error description: ${errorCodes[event.data] || 'Unknown error'}`);

        // 150/153はフォールバックしても改善しないことが多く、黒画面ループの原因になる。
        // この環境では埋め込み再生が成立しない可能性が高いので、通知を表示
        if (event.data === 150 || event.data === 153) {
            console.error(`Embed playback failed (code ${event.data}). Showing notification.`);
            this.showErrorNotification('この動画は再生できません（埋め込み制限）');
            return;
        }
        
        // 再生中なら(特に150/153)は無視する。実際に再生は継続しているケースがある。
        const recentlyPlaying = (Date.now() - (this._lastPlayingAtMs[playerId] || 0)) < 2500;
        if ((this._lastPlayerState[playerId] === YT.PlayerState.PLAYING || recentlyPlaying) && (event.data === 150 || event.data === 153)) {
            console.log(`Ignore error while playing (player ${playerId}, code ${event.data})`);
            return;
        }

        // エラー2は環境依存で出ることがあるため、フォールバック連打を避ける
        if (event.data === 2) {
            console.error('Error 2 (Invalid parameter) detected. Skip fallback and keep running.');
            return;
        }

        // 短時間の連続エラー(バースト)を検知
        const now = Date.now();
        const delta = now - (this._lastErrorAtMs[playerId] || 0);
        if (delta < 1500 && this._lastErrorCode[playerId] === event.data) {
            this._errorBurstCount[playerId] = (this._errorBurstCount[playerId] || 0) + 1;
        } else {
            this._errorBurstCount[playerId] = 1;
        }
        this._lastErrorAtMs[playerId] = now;
        this._lastErrorCode[playerId] = event.data;

        // 連続エラーが続く場合は手動モードへ
        if (this._errorBurstCount[playerId] >= 3) {
            console.error(`Too many repeated errors on player ${playerId}. Switching to manual playback.`);
            this.showManualPlayback();
            return;
        }

        // フォールバックは最大1回だけ試す
        if ((this._fallbackAttempts[playerId] || 0) >= 1) {
            console.error(`Fallback already attempted on player ${playerId}. Switching to manual playback.`);
            this.showManualPlayback();
            return;
        }

        console.log(`Attempting fallback video for error ${event.data}...`);
        this.tryFallbackVideo(playerId);
    }
    
    tryFallbackVideo(playerId) {
        // 埋め込み確実な代替動画を試す
        const fallbackVideoId = 'dQw4w9WgXcQ'; // Rick Roll

        this._fallbackAttempts[playerId] = (this._fallbackAttempts[playerId] || 0) + 1;
        
        try {
            this.players[playerId].loadVideoById({
                videoId: fallbackVideoId,
                startSeconds: 0,
                suggestedQuality: 'medium' // 品質を下げて安定性を向上
            });
            console.log(`Fallback video loaded for player ${playerId}: ${fallbackVideoId}`);
        } catch (error) {
            console.error(`Fallback video failed for player ${playerId}:`, error);
            // 代替動画も失敗した場合は手動再生案内を表示
            this.showManualPlayback();
        }
    }

    // コマンドポーリング開始
    startPolling() {
        console.log('Starting command polling...');
        this.pollingInterval = setInterval(() => {
            this.pollCommands();
        }, 100); // 100msごとにポーリング
    }

    // コマンドポーリング
    async pollCommands() {
        try {
            const response = await fetch(this.pollingUrl);
            const data = await response.json();
            
            if (data.cmd && data.cmd.trim()) {
                console.log('Received command:', data);
                this.processCommand(data);
            }
        } catch (error) {
            // ポーリングエラーを詳細表示
            console.error('Polling error:', error.message);
            console.log('Polling URL:', this.pollingUrl);
        }
    }

    // コマンド処理
    processCommand(command) {
        const cmd = command.cmd;
        const videoId = command.videoId;
        
        switch (cmd) {
            case 'PRELOAD':
                this.handlePreload(videoId);
                break;
            case 'PLAY':
                this.handlePlay(videoId);
                break;
            default:
                console.log('Unknown command:', cmd);
        }
    }

    // プリロード処理
    handlePreload(videoId) {
        if (!videoId || videoId === this.nextVideoId) {
            return;
        }

        console.log(`Preloading video: ${videoId}`);
        this.nextVideoId = videoId;
        
        // 状態フィードバックを送信
        this.sendFeedback('preloading', videoId);
        
        // 次のプレイヤーに動画をロード
        this.players[this.nextPlayer].cueVideoById({
            videoId: videoId,
            startSeconds: 0,
            suggestedQuality: 'hd720'
        });
        
        // プリロード完了後にready状態を送信
        setTimeout(() => {
            if (this.nextVideoId === videoId) {
                this.sendFeedback('ready', videoId);
                console.log(`Video ready: ${videoId}`);
            }
        }, 1000); // 1秒後にready状態を送信
    }

    // 再生処理
    handlePlay(videoId) {
        if (!videoId) {
            return;
        }

        console.log(`Playing video: ${videoId}`);
        console.log(`Current state: isReady[${this.nextPlayer}]=${this.isReady[this.nextPlayer]}, nextVideoId=${this.nextVideoId}`);
        
        if (this.isReady[this.nextPlayer] && this.nextVideoId === videoId) {
            // 準備完了している場合、即座に切り替え
            console.log('Ready - switching immediately');
            this.switchAndPlay(videoId);
        } else {
            // 準備完了していない場合、ロードしてから切り替え
            console.log('Not ready - loading and waiting');
            this.nextVideoId = videoId;
            this.players[this.nextPlayer].loadVideoById({
                videoId: videoId,
                startSeconds: 0,
                suggestedQuality: 'hd720'
            });
            
            // ロード完了を待って切り替え
            this.waitForReadyAndSwitch(videoId);
        }
    }

    // 準備完了を待って切り替え
    waitForReadyAndSwitch(videoId) {
        const checkReady = () => {
            if (this.isReady[this.nextPlayer] && this.nextVideoId === videoId) {
                this.switchAndPlay(videoId);
            } else {
                setTimeout(checkReady, 100);
            }
        };
        checkReady();
    }

    // プレイヤー切り替えと再生
    switchAndPlay(videoId) {
        console.log(`Switching to player ${this.nextPlayer} with video: ${videoId}`);
        
        // DOMの準備状態を確認
        console.log('DOM ready state:', document.readyState);
        console.log('Available containers:', {
            playerA: document.getElementById('playerAContainer'),
            playerB: document.getElementById('playerBContainer')
        });
        
        // 現在のプレイヤーを停止
        if (this.currentVideoId) {
            this.players[this.currentPlayer].stopVideo();
        }
        
        // 状態更新（切り替え前に実行）
        const oldPlayer = this.currentPlayer;
        this.currentVideoId = videoId;
        this.currentPlayer = this.nextPlayer;
        this.nextPlayer = this.nextPlayer === 'A' ? 'B' : 'A';
        this.nextVideoId = null;
        
        // プレイヤー切り替え（新しい状態で）
        const currentContainer = document.getElementById(`player${oldPlayer}Container`);
        const nextContainer = document.getElementById(`player${this.currentPlayer}Container`);
        
        console.log('Looking for containers:', {
            [`player${oldPlayer}Container`]: currentContainer,
            [`player${this.currentPlayer}Container`]: nextContainer
        });
        
        if (currentContainer && nextContainer) {
            console.log('Both containers found, switching visibility');
            // 現在のプレイヤーを背面に
            currentContainer.classList.remove('active');
            currentContainer.classList.add('hidden');
            
            // 次のプレイヤーを前面に
            nextContainer.classList.remove('hidden');
            nextContainer.classList.add('active');
        } else {
            console.error('Container elements not found:', {oldPlayer, currentPlayer: this.currentPlayer});
            
            // フォールバック：直接スタイルを操作
            if (currentContainer) {
                currentContainer.style.opacity = '0';
                currentContainer.style.pointerEvents = 'none';
            }
            if (nextContainer) {
                nextContainer.style.opacity = '1';
                nextContainer.style.pointerEvents = 'auto';
            }
        }
        
        // 新しい動画を再生
        this.players[this.currentPlayer].playVideo();
        
        // 状態フィードバックを送信
        this.sendFeedback('playing', videoId);
        
        console.log(`Switch complete. Current player: ${this.currentPlayer}`);
    }

    // ポーリングポート設定
    setPollingPort(port) {
        this.pollingPort = port;
        this.pollingUrl = `http://127.0.0.1:${port}/poll`;
        this.feedbackUrl = `http://127.0.0.1:${port}/feedback`;
        console.log(`Polling URL updated: ${this.pollingUrl}`);
    }
    
    // 状態フィードバック送信
    async sendFeedback(state, videoId) {
        try {
            const feedbackData = {
                state: state,
                videoId: videoId,
                timestamp: Date.now()
            };
            
            const response = await fetch(this.feedbackUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(feedbackData)
            });
            
            if (response.ok) {
                console.log(`Feedback sent: ${state} for ${videoId}`);
            } else {
                console.error(`Failed to send feedback: ${response.status}`);
            }
        } catch (error) {
            console.error('Feedback error:', error.message);
            console.log('Feedback URL:', this.feedbackUrl);
        }
    }

    // 破棄
    destroy() {
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
        }
        
        // プレイヤーの破棄
        Object.keys(this.players).forEach(playerId => {
            if (this.players[playerId]) {
                this.players[playerId].destroy();
            }
        });
        
        console.log('VJ Player destroyed');
    }
}

// 手動再生用の関数
function openVideo(videoId) {
    const url = `https://www.youtube.com/watch?v=${videoId}`;
    window.open(url, '_blank');
}

function hideManualOverlay() {
    document.getElementById('manualOverlay').style.display = 'none';
}

// VJ Playerのインスタンス作成
let vjPlayer;

// ページ読み込み完了時に初期化
window.addEventListener('load', () => {
    vjPlayer = new VJPlayer();
});

// ページアンロード時に破棄
window.addEventListener('beforeunload', () => {
    if (vjPlayer) {
        console.log('VJ Player destroyed');
    }
});

// デバッグ用：グローバルからアクセス可能に
window.vjPlayer = vjPlayer;
