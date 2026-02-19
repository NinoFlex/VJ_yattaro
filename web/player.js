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
        // 例: http://localhost:8080/player.html → 8080
        const pagePort = window.location.port ? parseInt(window.location.port, 10) : 80;
        this.pollingPort = Number.isFinite(pagePort) ? pagePort : 8080;
        this.pollingUrl = `${window.location.protocol}//${window.location.hostname}:${this.pollingPort}/poll`;
        this.feedbackUrl = `${window.location.protocol}//${window.location.hostname}:${this.pollingPort}/feedback`;
        
        // デフォルト動画（起動時に自動再生）
        // player側で固定値を持たず、ツール側が player.html のクエリで渡す
        // 例: http://localhost:8080/player.html?defaultVideoId=xxxxxxxxxxx
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
                    enablejsapi: 1,
                    mute: 1,
                    playsinline: 1,
                    origin: window.location.origin,
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
                    enablejsapi: 1,
                    mute: 1,
                    playsinline: 1,
                    origin: window.location.origin,
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
            console.error(`Embed playback failed (code ${event.data}). Attempt iframe fallback.`);
            // 埋め込み制限が疑われる場合、iframe 直接埋め込みでフォールバックを試みる
            const vid = this.currentVideoId || this.nextVideoId || null;
            if (vid) {
                const alreadyAttempted = this._fallbackAttempts[playerId] && this._fallbackAttempts[playerId] > 0;
                if (!alreadyAttempted) {
                    this._fallbackAttempts[playerId] = (this._fallbackAttempts[playerId] || 0) + 1;
                    this.tryEmbedFallback(playerId, vid);
                    return;
                }
            }
            // フォールバック失敗なら通知
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

    tryEmbedFallback(playerId, videoId) {
        try {
            console.log(`Attempting iframe embed fallback for player ${playerId}, video ${videoId}`);

            const container = document.getElementById(`player${playerId}Container`);
            if (!container) {
                console.error('Embed fallback: container not found for', playerId);
                return;
            }

            // 既に iframe フォールバック済みなら何もしない
            if (container.dataset && container.dataset.embedFallback === '1') {
                console.log('Embed fallback already applied for', playerId);
                return;
            }

            // iframe を作成して直接埋め込む
            const iframe = document.createElement('iframe');
            const origin = window.location.origin || (window.location.protocol + '//' + window.location.hostname);
            const src = `https://www.youtube.com/embed/${encodeURIComponent(videoId)}?autoplay=1&mute=1&playsinline=1&rel=0&enablejsapi=1&origin=${encodeURIComponent(origin)}`;
            iframe.setAttribute('src', src);
            iframe.setAttribute('frameborder', '0');
            iframe.setAttribute('allow', 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share');
            iframe.setAttribute('allowfullscreen', '');
            iframe.style.width = '100%';
            iframe.style.height = '100%';
            iframe.style.border = '0';

            // 既存のプレイヤー要素を隠す
            const playerElem = container.querySelector(`#player${playerId}`);
            if (playerElem) {
                playerElem.style.display = 'none';
            }

            // 既存の YT.Player インスタンスがあれば破棄して参照をクリア
            try {
                if (this.players && this.players[playerId] && typeof this.players[playerId].destroy === 'function') {
                    console.log('Destroying existing YT.Player for', playerId);
                    try {
                        this.players[playerId].destroy();
                    } catch (e) {
                        console.warn('Error while destroying YT.Player:', e);
                    }
                    this.players[playerId] = null;
                    this.isReady[playerId] = false;
                }
            } catch (e) {
                console.warn('Failed to clear existing player instance:', e);
            }

            // container の先頭に iframe を追加
            container.insertBefore(iframe, container.firstChild);

            // マークして重複適用を防ぐ
            if (!container.dataset) container.dataset = {};
            container.dataset.embedFallback = '1';

            console.log('Embed fallback applied for', playerId);
        } catch (e) {
            console.error('Embed fallback failed:', e);
            // 最終的に通知表示
            this.showErrorNotification('この動画は再生できません（フォールバック失敗）');
        }
    }

    // iframe を作成または更新して埋め込み再生する（フォールバック用）
    ensureIframeFor(playerId, videoId, autoplay = false) {
        try {
            const container = document.getElementById(`player${playerId}Container`);
            if (!container) return false;

            // 既存の iframe を取得
            let iframe = container.querySelector('iframe');
            const origin = window.location.origin || (window.location.protocol + '//' + window.location.hostname);
            const autoplayParam = autoplay ? '1' : '0';

            // 試行するホスト/パラメータの候補（順に試す）
            const candidates = [];

            // もし渡された videoId がフル URL なら、可能な限り元のクエリを埋め込みに反映する
            try {
                let parsedUrl = null;
                if (/^https?:\/\//i.test(videoId)) {
                    parsedUrl = new URL(videoId);
                }

                if (parsedUrl) {
                    // YouTube の watch URL や youtu.be 短縮 URL を解析
                    let vid = null;
                    const sp = parsedUrl.searchParams;
                    if (sp.has('v')) {
                        vid = sp.get('v');
                    } else {
                        // youtu.be の場合 path の先頭が video id
                        const p = parsedUrl.pathname.split('/').filter(Boolean);
                        if (p.length > 0) vid = p[p.length - 1];
                    }

                    // 追加可能なパラメータを列挙して埋め込用に転記
                    const extraKeys = ['list', 'start_radio', 'pp', 't', 'start', 'index'];
                    const extraParams = [];
                    for (const k of extraKeys) {
                        if (sp.has(k)) {
                            extraParams.push(`${encodeURIComponent(k)}=${encodeURIComponent(sp.get(k))}`);
                        }
                    }

                    if (vid) {
                        const baseParams = `autoplay=${autoplayParam}&mute=1&playsinline=1&rel=0&enablejsapi=0&origin=${encodeURIComponent(origin)}`;
                        const extras = extraParams.length ? `&${extraParams.join('&')}` : '';
                        const src = `https://www.youtube.com/embed/${encodeURIComponent(vid)}?${baseParams}${extras}`;
                        candidates.push({src});
                    }
                }
            } catch (e) {
                console.warn('ensureIframeFor: failed to parse provided URL', e);
            }

            // デフォルト候補
            candidates.push({src: `https://www.youtube.com/embed/${encodeURIComponent(videoId)}?autoplay=${autoplayParam}&mute=1&playsinline=1&rel=0&enablejsapi=0&origin=${encodeURIComponent(origin)}`});
            candidates.push({src: `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}?autoplay=${autoplayParam}&mute=1&playsinline=1&rel=0&enablejsapi=0&origin=${encodeURIComponent(origin)}`});
            candidates.push({src: `https://www.youtube.com/embed/${encodeURIComponent(videoId)}?autoplay=${autoplayParam}&mute=1&playsinline=1&rel=0&enablejsapi=0&origin=${encodeURIComponent(origin)}&widget_referrer=${encodeURIComponent(window.location.href)}`});

            const applySrc = (srcUrl) => {
                if (iframe) {
                    iframe.src = srcUrl;
                } else {
                    iframe = document.createElement('iframe');
                    iframe.setAttribute('src', srcUrl);
                    iframe.setAttribute('frameborder', '0');
                    iframe.setAttribute('allow', 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; autoplay');
                    iframe.setAttribute('allowfullscreen', '');
                    iframe.style.width = '100%';
                    iframe.style.height = '100%';
                    iframe.style.border = '0';
                    container.insertBefore(iframe, container.firstChild);
                }
                container.dataset.embedFallback = '1';
                container.dataset.embedVideoId = videoId;
            };

            // 既存 iframe が目的の動画であれば autoplay のみ更新して終わり
            if (iframe && iframe.src && iframe.src.indexOf(`/embed/${encodeURIComponent(videoId)}`) !== -1) {
                if (autoplay) {
                    // 強制的に src を差し替えて autoplay を適用
                    const primary = `https://www.youtube.com/embed/${encodeURIComponent(videoId)}?autoplay=${autoplayParam}&mute=1&playsinline=1&rel=0&enablejsapi=0&origin=${encodeURIComponent(origin)}`;
                    iframe.src = primary;
                }
                container.dataset.embedFallback = '1';
                container.dataset.embedVideoId = videoId;
                return true;
            }

            // 候補を順に適用（最初に DOM 操作に成功したものを使用）
            for (let c of candidates) {
                try {
                    applySrc(c.src);
                    console.log('ensureIframeFor: applied candidate src:', c.src);
                    break; // DOMに適用できたら終了（ブラウザ側で再生可否が決まる）
                } catch (e) {
                    console.warn('ensureIframeFor: failed to apply candidate', c.src, e);
                    // 次の候補へ
                }
            }

            // 既存の YT.Player を隠す/破棄
            const playerElem = container.querySelector(`#player${playerId}`);
            if (playerElem) playerElem.style.display = 'none';
            try {
                if (this.players && this.players[playerId] && typeof this.players[playerId].destroy === 'function') {
                    this.players[playerId].destroy();
                    this.players[playerId] = null;
                    this.isReady[playerId] = false;
                }
            } catch (e) {
                console.warn('ensureIframeFor: failed to destroy player', e);
            }

            container.dataset.embedFallback = '1';
            container.dataset.embedVideoId = videoId;

            return true;
        } catch (e) {
            console.error('ensureIframeFor error:', e);
            return false;
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

        // 次のプレイヤーが存在する場合は通常の cue を使う
        const nextPlayerObj = this.players[this.nextPlayer];
        if (nextPlayerObj && typeof nextPlayerObj.cueVideoById === 'function') {
            try {
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
            } catch (e) {
                console.warn('cueVideoById failed, falling back to iframe:', e);
                // iframe フォールバックとして埋め込む（autoplayはしない）
                if (this.ensureIframeFor(this.nextPlayer, videoId, false)) {
                    // iframe の場合は即 ready 扱いにする
                    this.sendFeedback('ready', videoId);
                    console.log(`Iframe preload ready: ${videoId}`);
                } else {
                    this.showManualPlayback();
                }
            }
        } else {
            // YT.Playerが無い（フォールバック済み）なら iframe を作成して ready 扱い
            if (this.ensureIframeFor(this.nextPlayer, videoId, false)) {
                this.sendFeedback('ready', videoId);
                console.log(`Iframe preload ready: ${videoId}`);
            } else {
                this.showManualPlayback();
            }
        }
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
            return;
        }

        // 準備完了していない場合、YT.Player があれば load、なければ iframe フォールバックを作る
        console.log('Not ready - loading and waiting');
        this.nextVideoId = videoId;

        const nextPlayerObj = this.players[this.nextPlayer];
        if (nextPlayerObj && typeof nextPlayerObj.loadVideoById === 'function') {
            try {
                nextPlayerObj.loadVideoById({
                    videoId: videoId,
                    startSeconds: 0,
                    suggestedQuality: 'hd720'
                });
            } catch (e) {
                console.warn('loadVideoById failed, falling back to iframe:', e);
                // iframe に差し替えて自動再生を試みる
                if (this.ensureIframeFor(this.nextPlayer, videoId, true)) {
                    // iframe は即時切り替え可能とみなす
                    this.isReady[this.nextPlayer] = true;
                }
            }
        } else {
            // YT.Player が存在しない場合は iframe で再生を行う
            if (this.ensureIframeFor(this.nextPlayer, videoId, true)) {
                this.isReady[this.nextPlayer] = true;
            }
        }

        // ロード完了を待って切り替え
        this.waitForReadyAndSwitch(videoId);
    }

    // 準備完了を待って切り替え
    waitForReadyAndSwitch(videoId) {
        const checkReady = () => {
            // YT.Player が消えて iframe フォールバックになっている場合、iframe が目的の動画を指していれば即座に切替
            const nextContainer = document.getElementById(`player${this.nextPlayer}Container`);
            const iframePresent = nextContainer && nextContainer.dataset && nextContainer.dataset.embedFallback === '1' && nextContainer.dataset.embedVideoId === videoId;

            if ((this.isReady[this.nextPlayer] && this.nextVideoId === videoId) || iframePresent) {
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
        
        // 現在のプレイヤーを停止（存在チェック）
        if (this.currentVideoId) {
            const curPlayerObj = this.players[this.currentPlayer];
            if (curPlayerObj && typeof curPlayerObj.stopVideo === 'function') {
                try { curPlayerObj.stopVideo(); } catch (e) { console.warn('stopVideo failed:', e); }
            }
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
        
        // 新しい動画を再生（YT.Playerが存在する場合のみ）。
        const nextPlayerObj = this.players[this.currentPlayer];
        if (nextPlayerObj && typeof nextPlayerObj.playVideo === 'function') {
            try {
                nextPlayerObj.playVideo();
            } catch (e) {
                console.warn('playVideo failed:', e);
            }
        } else {
            console.log('No YT.Player for current player; assume iframe fallback will autoplay if present');
        }
        
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
