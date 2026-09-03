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
        // The desktop app creates a new token on every server start. Only the browser tab
        // opened for that token can poll commands, so old tabs cannot consume new-session input.
        this.sessionId = this.getControllerSessionFromQuery();
        this._pollInFlight = false;
        this._sessionMismatchShown = false;

        // デフォルト動画（起動時に自動再生）
        // player側で固定値を持たず、ツール側が player.html のクエリで渡す
        // 例: http://localhost:8080/player.html?defaultVideoId=xxxxxxxxxxx
        this.defaultVideoId = this.getDefaultVideoIdFromQuery();

        // エラーループ抑止
        this._lastErrorAtMs = { A: 0, B: 0 };
        this._errorBurstCount = { A: 0, B: 0 };
        this._lastErrorCode = { A: null, B: null };
        this._lastPlayerState = { A: null, B: null };
        this._lastPlayingAtMs = { A: 0, B: 0 };
        this.failedVideoIds = { A: null, B: null };

        // 楽曲情報の管理
        this.currentTrackInfo = null;
        this.nextTrackInfo = null;
        this.currentMediaInfo = null;
        this.nextMediaInfo = null;

        // Physical A/B player state for the desktop operation panels.
        this.playerVideoIds = { A: null, B: null };
        this.playerTrackInfo = { A: null, B: null };
        this.playerMediaInfo = { A: null, B: null };
        // 楽曲情報の表示位置（クエリパラメータまたはデフォルト: 右上）
        this.trackInfoPosition = this.getTrackInfoPositionFromQuery() || 'top-right';

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
                    onApiChange: (event) => this.onPlayerApiChange('A', event),
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
                    onApiChange: (event) => this.onPlayerApiChange('B', event),
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

    getControllerSessionFromQuery() {
        try {
            const params = new URLSearchParams(window.location.search);
            const value = params.get('controllerSession');
            return value && value.trim() ? value.trim() : '';
        } catch (e) {
            console.warn('Failed to parse controllerSession from query:', e);
            return '';
        }
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

    getTrackInfoPositionFromQuery() {
        try {
            const params = new URLSearchParams(window.location.search);
            const pos = params.get('trackInfoPosition');
            const valid = ['top-right', 'top-left', 'bottom-right', 'bottom-left', 'scroll', 'none'];
            return pos && valid.includes(pos.trim()) ? pos.trim() : null;
        } catch (e) {
            console.warn('Failed to parse trackInfoPosition from query:', e);
            return null;
        }
    }

    onPlayerReady(playerId, event) {
        console.log(`Player ${playerId} is ready`);
        this.isReady[playerId] = true;
        this.forceCaptionsOff(playerId);

        // 両方のプレイヤーが準備完了したら
        if (this.isReady.A && this.isReady.B) {
            console.log('Both players are ready');
            this.setupInitialStates();
        }
    }

    onPlayerApiChange(playerId, event) {
        // 字幕モジュールは動画ロード後に遅れて追加されることがあるため、
        // API構成が変わったタイミングでも字幕OFFを再適用する。
        this.forceCaptionsOff(playerId);
    }

    forceCaptionsOff(playerId) {
        const player = this.players[playerId];
        if (!player) {
            return;
        }

        try {
            // 旧IFrame API互換処理。現在も動作する環境では、選択中の字幕トラックを解除する。
            if (typeof player.setOption === 'function') {
                player.setOption('captions', 'track', {});
            }
        } catch (e) {
            // captionsモジュール未ロード時などは無視する。
        }

        try {
            // cc_load_policy=0 はユーザー設定に従うだけなので、
            // 利用可能な環境では字幕モジュール自体もアンロードしてOFFを優先する。
            if (typeof player.unloadModule === 'function') {
                player.unloadModule('captions');
                player.unloadModule('cc');
            }
        } catch (e) {
            // YouTube側の実装差異で失敗しても再生には影響させない。
        }
    }

    setupInitialStates() {
        // プレイヤーAを前面に、プレイヤーBを背面に配置
        document.getElementById('playerAContainer').classList.add('active');
        document.getElementById('playerBContainer').classList.add('hidden');

        // 楽曲情報の表示位置を初期化時に適用
        this.applyTrackInfoPosition();

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
        console.log(`Playing default video: ${this.defaultVideoId}`);

        try {
            this.players.A.loadVideoById({
                videoId: this.defaultVideoId,
                startSeconds: 0,
                suggestedQuality: 'medium'
            });

            this.currentVideoId = this.defaultVideoId;
            this.currentPlayer = 'A';
            this.nextPlayer = 'B';
            this.playerVideoIds.A = this.defaultVideoId;
            this.playerMediaInfo.A = {
                videoTitle: '',
                thumbnailUrl: `https://i.ytimg.com/vi/${this.defaultVideoId}/hqdefault.jpg`,
                durationText: ''
            };

            this.sendFeedback('playing', this.defaultVideoId, 'A');
            console.log(`Default video started: ${this.defaultVideoId}`);
        } catch (error) {
            console.error('Error playing default video:', error);
            this.showManualPlayback();
        }
    }

    onPlayerStateChange(playerId, event) {
        const state = event.data;
        this._lastPlayerState[playerId] = state;
        const stateVideoId = this.getVideoIdForPlayer(playerId);
        if (this.failedVideoIds[playerId] && stateVideoId === this.failedVideoIds[playerId]) {
            // stopVideo() after an error may emit ENDED/UNSTARTED. Do not let those states
            // overwrite the ERROR panel or restart the failed loop.
            if (state !== YT.PlayerState.PLAYING) {
                return;
            }
        }
        if (state === YT.PlayerState.PLAYING) {
            this._lastPlayingAtMs[playerId] = Date.now();
        }
        console.log(`Player ${playerId} state changed: ${state}`);

        if (state === YT.PlayerState.CUED || state === YT.PlayerState.PLAYING || state === YT.PlayerState.PAUSED) {
            this.isReady[playerId] = true;
            this.forceCaptionsOff(playerId);
            setTimeout(() => this.forceCaptionsOff(playerId), 250);
            setTimeout(() => this.forceCaptionsOff(playerId), 1000);
        } else if (state === YT.PlayerState.UNSTARTED) {
            this.isReady[playerId] = false;
        }

        const stateNames = {};
        stateNames[YT.PlayerState.UNSTARTED] = 'unstarted';
        stateNames[YT.PlayerState.ENDED] = 'ended';
        stateNames[YT.PlayerState.PLAYING] = 'playing';
        stateNames[YT.PlayerState.PAUSED] = 'paused';
        stateNames[YT.PlayerState.BUFFERING] = 'buffering';
        stateNames[YT.PlayerState.CUED] = 'ready';
        const feedbackState = stateNames[state];
        if (feedbackState) {
            this.sendFeedback(feedbackState, this.getVideoIdForPlayer(playerId), playerId);
        }

        // Loop only the player currently visible on the output.
        if (state === YT.PlayerState.ENDED && playerId === this.currentPlayer) {
            this.players[playerId].playVideo();
        }
    }

    onPlayerError(playerId, event) {
        const errorCode = Number(event && event.data);
        const videoId = this.getVideoIdForPlayer(playerId);
        const errorCodes = {
            2: 'Invalid parameter',
            5: 'HTML5 player error',
            100: 'Video not found or removed',
            101: 'Video not embeddable',
            150: 'Video not embeddable',
            153: 'Video not embeddable or restricted'
        };
        const description = errorCodes[errorCode] || 'Unknown error';
        console.error(`Player ${playerId} failed: code=${errorCode} ${description}`);

        // Never replace a failed request with an unrelated video. Stop only the failed
        // physical player, black its output, and report the original video as unavailable.
        this.markPlaybackFailed(playerId, videoId, errorCode);
    }

    markPlaybackFailed(playerId, videoId, errorCode) {
        const targetPlayer = this.normalizePlayerId(playerId);
        this.isReady[targetPlayer] = false;
        this.failedVideoIds[targetPlayer] = videoId || this.playerVideoIds[targetPlayer] || null;

        if (this.nextPlayer === targetPlayer && this.nextVideoId === videoId) {
            // This also terminates waitForReadyAndSwitch() because its request is now stale.
            this.nextVideoId = null;
        }

        const player = this.players[targetPlayer];
        if (player && typeof player.stopVideo === 'function') {
            try {
                player.stopVideo();
            } catch (e) {
                console.warn(`Failed to stop player ${targetPlayer} after playback error:`, e);
            }
        }

        const container = document.getElementById(`player${targetPlayer}Container`);
        if (container) {
            container.classList.add('playback-error');
        }

        this.sendFeedback('error', videoId || '', targetPlayer, {
            errorCode: errorCode,
            includeTiming: false
        });
        this.showErrorNotification(`Player ${targetPlayer}: この動画は再生できません`);
    }

    clearPlaybackError(playerId) {
        const targetPlayer = this.normalizePlayerId(playerId);
        this.failedVideoIds[targetPlayer] = null;
        const container = document.getElementById(`player${targetPlayer}Container`);
        if (container) {
            container.classList.remove('playback-error');
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
            const src = `https://www.youtube.com/embed/${encodeURIComponent(videoId)}?autoplay=1&mute=1&playsinline=1&rel=0&cc_load_policy=0&enablejsapi=1&origin=${encodeURIComponent(origin)}`;
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
                        const baseParams = `autoplay=${autoplayParam}&mute=1&playsinline=1&rel=0&cc_load_policy=0&enablejsapi=0&origin=${encodeURIComponent(origin)}`;
                        const extras = extraParams.length ? `&${extraParams.join('&')}` : '';
                        const src = `https://www.youtube.com/embed/${encodeURIComponent(vid)}?${baseParams}${extras}`;
                        candidates.push({ src });
                    }
                }
            } catch (e) {
                console.warn('ensureIframeFor: failed to parse provided URL', e);
            }

            // デフォルト候補
            candidates.push({ src: `https://www.youtube.com/embed/${encodeURIComponent(videoId)}?autoplay=${autoplayParam}&mute=1&playsinline=1&rel=0&cc_load_policy=0&enablejsapi=0&origin=${encodeURIComponent(origin)}` });
            candidates.push({ src: `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}?autoplay=${autoplayParam}&mute=1&playsinline=1&rel=0&cc_load_policy=0&enablejsapi=0&origin=${encodeURIComponent(origin)}` });
            candidates.push({ src: `https://www.youtube.com/embed/${encodeURIComponent(videoId)}?autoplay=${autoplayParam}&mute=1&playsinline=1&rel=0&cc_load_policy=0&enablejsapi=0&origin=${encodeURIComponent(origin)}&widget_referrer=${encodeURIComponent(window.location.href)}` });

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
                    const primary = `https://www.youtube.com/embed/${encodeURIComponent(videoId)}?autoplay=${autoplayParam}&mute=1&playsinline=1&rel=0&cc_load_policy=0&enablejsapi=0&origin=${encodeURIComponent(origin)}`;
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
        if (!this.sessionId) {
            console.error('Controller session token is missing.');
            this.showErrorNotification('コントローラーとの接続情報がありません。プレイヤーをツールから開き直してください。');
            return;
        }
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
        }
        console.log('Starting command polling...');
        this._lastHeartbeatAt = 0;
        this.pollingInterval = setInterval(() => {
            this.pollCommands();
        }, 100);
    }

    stopPollingForSessionMismatch() {
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }
        if (!this._sessionMismatchShown) {
            this._sessionMismatchShown = true;
            this.showErrorNotification('このプレイヤータブは古いセッションです。ツールから開いた新しいタブを使用してください。');
        }
    }

    // コマンドポーリング。前回のHTTP要求が終わるまで次要求を出さない。
    async pollCommands() {
        if (this._pollInFlight || !this.sessionId) {
            return;
        }
        this._pollInFlight = true;
        try {
            const now = Date.now();
            if (now - this._lastHeartbeatAt > 5000) {
                this.sendFeedback('HEARTBEAT', this.currentVideoId || '', this.currentPlayer, { includeTiming: false, includeMetadata: false });
                this._lastHeartbeatAt = now;
            }

            const pollUrl = `${this.pollingUrl}?sessionId=${encodeURIComponent(this.sessionId)}`;
            const response = await fetch(pollUrl, { cache: 'no-store' });
            const data = await response.json();

            if (data.sessionMismatch) {
                this.stopPollingForSessionMismatch();
                return;
            }

            if (data.cmd && data.cmd.trim()) {
                console.log('Received command:', data.cmd, data.commandId || '');
                let status = 'accepted';
                let errorMessage = '';
                try {
                    this.processCommand(data);
                } catch (error) {
                    status = 'error';
                    errorMessage = error && error.message ? error.message : String(error);
                    console.error('Command processing error:', error);
                }
                if (data.commandId) {
                    this.sendCommandAck(data, status, errorMessage);
                }
            }
        } catch (error) {
            console.error('Polling error:', error.message);
        } finally {
            this._pollInFlight = false;
        }
    }

    sendCommandAck(command, status = 'accepted', errorMessage = '') {
        return this.sendFeedback('command_ack', command.videoId || '', command.playerId || null, {
            includeTiming: false,
            includeMetadata: false,
            commandId: command.commandId || '',
            commandStatus: status,
            commandError: errorMessage || ''
        });
    }

    // コマンド処理
    processCommand(command) {
        const cmd = command.cmd;
        const videoId = command.videoId;
        const trackInfo = command.trackInfo || null;
        const mediaInfo = command.mediaInfo || null;
        const playerId = this.normalizePlayerId(command.playerId);

        switch (cmd) {
            case 'PRELOAD':
                this.handlePreload(videoId, trackInfo, mediaInfo);
                break;
            case 'PLAY':
                this.handlePlay(videoId, trackInfo, mediaInfo);
                break;
            case 'REWIND':
                this.handleRewind(parseFloat(videoId) || 10, playerId);
                break;
            case 'FORWARD':
                this.handleForward(parseFloat(videoId) || 10, playerId);
                break;
            case 'PAUSE_PLAYER':
                this.handlePause(playerId);
                break;
            case 'RESUME_PLAYER':
                this.handleResume(playerId);
                break;
            case 'SELECT_PLAYER':
                this.handleSelectPlayer(playerId);
                break;
            case 'REQUEST_PLAYER_STATE':
                this.sendAllPlayerSnapshots();
                break;
            case 'SET_CONFIG':
                this.handleSetConfig(videoId);
                break;
            default:
                console.log('Unknown command:', cmd);
        }
    }

    // PRELOAD always targets the player currently reserved as nextPlayer.

    handlePreload(videoId, trackInfo, mediaInfo) {
        const targetPlayer = this.nextPlayer;
        if (!videoId) {
            return;
        }
        if (videoId === this.nextVideoId && this.playerVideoIds[targetPlayer] === videoId) {
            this.sendPlayerSnapshot(targetPlayer);
            return;
        }

        console.log(`Preloading video ${videoId} into player ${targetPlayer}`);
        this.clearPlaybackError(targetPlayer);
        this.nextVideoId = videoId;
        this.playerVideoIds[targetPlayer] = videoId;
        if (trackInfo) {
            this.nextTrackInfo = trackInfo;
            this.playerTrackInfo[targetPlayer] = trackInfo;
        }
        if (mediaInfo) {
            this.nextMediaInfo = mediaInfo;
            this.playerMediaInfo[targetPlayer] = mediaInfo;
        }

        this.sendFeedback('preloading', videoId, targetPlayer, { includeTiming: false });
        this.isReady[targetPlayer] = false;

        const nextPlayerObj = this.players[targetPlayer];
        if (nextPlayerObj && typeof nextPlayerObj.cueVideoById === 'function') {
            try {
                nextPlayerObj.cueVideoById({
                    videoId: videoId,
                    startSeconds: 0,
                    suggestedQuality: 'hd720'
                });

                // Some browsers delay or omit CUED; keep a one-shot fallback notification.
                setTimeout(() => {
                    if (this.nextVideoId === videoId && this.nextPlayer === targetPlayer) {
                        this.sendFeedback('ready', videoId, targetPlayer);
                        console.log(`Video ready: ${videoId} on player ${targetPlayer}`);
                    }
                }, 1000);
            } catch (e) {
                console.warn('cueVideoById failed, falling back to iframe:', e);
                if (this.ensureIframeFor(targetPlayer, videoId, false)) {
                    this.sendFeedback('ready', videoId, targetPlayer, { includeTiming: false });
                } else {
                    this.showManualPlayback();
                }
            }
        } else {
            if (this.ensureIframeFor(targetPlayer, videoId, false)) {
                this.sendFeedback('ready', videoId, targetPlayer, { includeTiming: false });
            } else {
                this.showManualPlayback();
            }
        }
    }

    // PLAY switches the prepared physical player to the output.

    handlePlay(videoId, trackInfo, mediaInfo) {
        if (!videoId) {
            return;
        }

        const targetPlayer = this.nextPlayer;
        console.log(`Playing video ${videoId} on target player ${targetPlayer}`);
        this.clearPlaybackError(targetPlayer);
        this.playerVideoIds[targetPlayer] = videoId;
        if (trackInfo) {
            this.nextTrackInfo = trackInfo;
            this.playerTrackInfo[targetPlayer] = trackInfo;
        }
        if (mediaInfo) {
            this.nextMediaInfo = mediaInfo;
            this.playerMediaInfo[targetPlayer] = mediaInfo;
        }

        if (this.isReady[targetPlayer] && this.nextVideoId === videoId) {
            this.switchAndPlay(videoId, targetPlayer);
            return;
        }

        this.nextVideoId = videoId;
        this.isReady[targetPlayer] = false;
        const nextPlayerObj = this.players[targetPlayer];
        if (nextPlayerObj && typeof nextPlayerObj.loadVideoById === 'function') {
            try {
                nextPlayerObj.loadVideoById({
                    videoId: videoId,
                    startSeconds: 0,
                    suggestedQuality: 'hd720'
                });
            } catch (e) {
                console.warn('loadVideoById failed, falling back to iframe:', e);
                if (this.ensureIframeFor(targetPlayer, videoId, true)) {
                    this.isReady[targetPlayer] = true;
                }
            }
        } else if (this.ensureIframeFor(targetPlayer, videoId, true)) {
            this.isReady[targetPlayer] = true;
        }

        this.waitForReadyAndSwitch(videoId, targetPlayer);
    }

    // Wait for the same physical target captured when PLAY was received.

    waitForReadyAndSwitch(videoId, targetPlayer) {
        const checkReady = () => {
            // A newer PRELOAD/PLAY replaced this request; do not switch the stale video.
            if (this.nextPlayer !== targetPlayer || this.nextVideoId !== videoId) {
                return;
            }
            const nextContainer = document.getElementById(`player${targetPlayer}Container`);
            const iframePresent = nextContainer && nextContainer.dataset &&
                nextContainer.dataset.embedFallback === '1' &&
                nextContainer.dataset.embedVideoId === videoId;

            if (this.isReady[targetPlayer] || iframePresent) {
                this.switchAndPlay(videoId, targetPlayer);
            } else {
                setTimeout(checkReady, 100);
            }
        };
        checkReady();
    }

    normalizePlayerId(playerId) {
        const normalized = String(playerId || '').toUpperCase();
        return normalized === 'A' || normalized === 'B' ? normalized : this.currentPlayer;
    }

    getVideoIdForPlayer(playerId) {
        const normalized = this.normalizePlayerId(playerId);
        // While PRELOAD/PLAY is loading, prefer the requested ID over stale API data.
        if (normalized === this.nextPlayer && this.nextVideoId) {
            return this.nextVideoId;
        }
        const player = this.players[normalized];
        try {
            if (player && typeof player.getVideoData === 'function') {
                const data = player.getVideoData();
                if (data && data.video_id) {
                    this.playerVideoIds[normalized] = data.video_id;
                    return data.video_id;
                }
            }
        } catch (e) {
            // During iframe/player transitions the API may be temporarily unavailable.
        }
        return this.playerVideoIds[normalized] ||
            (normalized === this.currentPlayer ? this.currentVideoId : null) || '';
    }

    refreshPlayerMediaInfoFromIframe(playerId, expectedVideoId = '') {
        const normalized = this.normalizePlayerId(playerId);
        const existing = Object.assign({}, this.playerMediaInfo[normalized] || {});
        const player = this.players[normalized];
        try {
            if (player && typeof player.getVideoData === 'function') {
                const data = player.getVideoData();
                const actualVideoId = data && data.video_id ? String(data.video_id) : '';
                const expected = String(expectedVideoId || '');

                // During PRELOAD the physical iframe can still report the old
                // video's title for a moment. Never let that stale title replace
                // the metadata of the newly requested video.
                const matchesRequestedVideo = !expected || !actualVideoId || actualVideoId === expected;
                if (matchesRequestedVideo && data && data.title) {
                    existing.videoTitle = String(data.title);
                }
                if (matchesRequestedVideo && actualVideoId && !existing.thumbnailUrl) {
                    existing.thumbnailUrl = `https://i.ytimg.com/vi/${actualVideoId}/hqdefault.jpg`;
                }
            }
        } catch (e) {
            // Player data can be temporarily unavailable during A/B transitions.
        }
        this.playerMediaInfo[normalized] = existing;
        return existing;
    }

    playerStateName(playerId) {
        const normalized = this.normalizePlayerId(playerId);
        const state = this._lastPlayerState[normalized];
        if (typeof YT !== 'undefined' && YT.PlayerState) {
            if (state === YT.PlayerState.PLAYING) return 'playing';
            if (state === YT.PlayerState.PAUSED) return 'paused';
            if (state === YT.PlayerState.BUFFERING) return 'buffering';
            if (state === YT.PlayerState.CUED) return 'ready';
            if (state === YT.PlayerState.ENDED) return 'ended';
            if (state === YT.PlayerState.UNSTARTED) return 'unstarted';
        }
        return this.playerVideoIds[normalized] ? 'ready' : 'idle';
    }

    handleSelectPlayer(playerId) {
        const targetPlayer = this.normalizePlayerId(playerId);
        if (targetPlayer !== 'A' && targetPlayer !== 'B') {
            return;
        }

        if (targetPlayer === this.currentPlayer) {
            this.sendPlayerSnapshot(targetPlayer);
            return;
        }

        const oldPlayer = this.currentPlayer;
        const oldPlayerObj = this.players[oldPlayer];
        const oldContainer = document.getElementById(`player${oldPlayer}Container`);
        const targetContainer = document.getElementById(`player${targetPlayer}Container`);
        const selectedVideoId = this.getVideoIdForPlayer(targetPlayer);

        // Only one output should make sound.  Preserve the old player's
        // position by pausing rather than stopping it when the visible side is
        // changed with the A/B toggle.
        try {
            if (oldPlayerObj && typeof oldPlayerObj.pauseVideo === 'function') {
                oldPlayerObj.pauseVideo();
            }
        } catch (e) {
            console.warn(`Failed to pause previous visible player ${oldPlayer}:`, e);
        }

        if (oldContainer) {
            oldContainer.classList.remove('active', 'crossfade-in', 'crossfade-out');
            oldContainer.classList.add('hidden');
            oldContainer.style.opacity = '0';
            oldContainer.style.pointerEvents = 'none';
        }
        if (targetContainer) {
            targetContainer.classList.remove('hidden', 'crossfade-in', 'crossfade-out');
            targetContainer.classList.add('active');
            targetContainer.style.opacity = '1';
            targetContainer.style.pointerEvents = 'auto';
        }

        // Consume the old "next" slot if that is the side the user selected.
        // The opposite physical player becomes the next preload destination.
        const selectedWasNext = targetPlayer === this.nextPlayer;
        this.currentPlayer = targetPlayer;
        this.nextPlayer = targetPlayer === 'A' ? 'B' : 'A';
        this.currentVideoId = selectedVideoId || '';
        if (selectedWasNext) {
            this.nextVideoId = null;
        }
        this.currentTrackInfo = this.playerTrackInfo[targetPlayer] || null;
        this.currentMediaInfo = this.playerMediaInfo[targetPlayer] || null;
        this.updateTrackInfoOverlay();

        // Let pauseVideo/state-change settle, then report both sides so the Qt
        // panels update their current marker and state consistently.
        setTimeout(() => {
            this.sendPlayerSnapshot(oldPlayer);
            this.sendPlayerSnapshot(targetPlayer);
        }, 80);
        console.log(`Visible player selected by controller toggle: ${targetPlayer}`);
    }

    handlePause(playerId) {
        const targetPlayer = this.normalizePlayerId(playerId);
        const player = this.players[targetPlayer];
        if (player && typeof player.pauseVideo === 'function') {
            try {
                player.pauseVideo();
                setTimeout(() => this.sendPlayerSnapshot(targetPlayer, 'paused'), 50);
                return;
            } catch (error) {
                console.error(`Pause failed for player ${targetPlayer}:`, error);
            }
        }
        this.sendFeedback('control_unavailable', this.getVideoIdForPlayer(targetPlayer), targetPlayer, { includeTiming: false });
    }

    handleResume(playerId) {
        const targetPlayer = this.normalizePlayerId(playerId);
        const player = this.players[targetPlayer];
        if (player && typeof player.playVideo === 'function') {
            try {
                player.playVideo();
                setTimeout(() => this.sendPlayerSnapshot(targetPlayer, 'playing'), 50);
                return;
            } catch (error) {
                console.error(`Resume failed for player ${targetPlayer}:`, error);
            }
        }
        this.sendFeedback('control_unavailable', this.getVideoIdForPlayer(targetPlayer), targetPlayer, { includeTiming: false });
    }

    handleSeekDelta(seconds, playerId) {
        const targetPlayer = this.normalizePlayerId(playerId);
        const player = this.players[targetPlayer];
        if (!player || typeof player.seekTo !== 'function' || typeof player.getCurrentTime !== 'function') {
            this.sendFeedback('control_unavailable', this.getVideoIdForPlayer(targetPlayer), targetPlayer, { includeTiming: false });
            return;
        }

        try {
            const currentTime = Number(player.getCurrentTime()) || 0;
            const duration = typeof player.getDuration === 'function' ? Number(player.getDuration()) || 0 : 0;
            let newTime = Math.max(0, currentTime + Number(seconds || 0));
            if (duration > 0) {
                newTime = Math.min(duration, newTime);
            }
            player.seekTo(newTime, true);
            console.log(`Player ${targetPlayer} seeked to ${newTime} seconds`);
            setTimeout(() => this.sendPlayerSnapshot(targetPlayer), 50);
        } catch (error) {
            console.error(`Seek failed for player ${targetPlayer}:`, error);
        }
    }

    handleRewind(seconds, playerId) {
        console.log(`Rewinding player ${this.normalizePlayerId(playerId)} by ${seconds} seconds`);
        this.handleSeekDelta(-Math.abs(seconds), playerId);
    }

    handleForward(seconds, playerId) {
        console.log(`Forwarding player ${this.normalizePlayerId(playerId)} by ${seconds} seconds`);
        this.handleSeekDelta(Math.abs(seconds), playerId);
    }

    // Switch the selected physical player to the visible output.

    switchAndPlay(videoId, targetPlayer = null) {
        const selectedPlayer = targetPlayer || this.nextPlayer;
        this.clearPlaybackError(selectedPlayer);
        console.log(`Switching to player ${selectedPlayer} with video: ${videoId}`);

        const oldPlayer = this.currentPlayer;
        const oldPlayerObj = this.players[oldPlayer];
        const wasPreloaded = this.nextVideoId === videoId && this.nextPlayer === selectedPlayer;

        this.currentVideoId = videoId;
        this.currentPlayer = selectedPlayer;
        this.nextPlayer = selectedPlayer === 'A' ? 'B' : 'A';
        this.nextVideoId = null;
        this.playerVideoIds[selectedPlayer] = videoId;

        const currentContainer = document.getElementById(`player${oldPlayer}Container`);
        const nextContainer = document.getElementById(`player${selectedPlayer}Container`);

        if (currentContainer && nextContainer) {
            const nextPlayerObj = this.players[selectedPlayer];
            let videoStarted = false;

            if (nextPlayerObj && typeof nextPlayerObj.playVideo === 'function') {
                try {
                    nextPlayerObj.playVideo();
                    videoStarted = true;
                } catch (e) {
                    console.warn('playVideo failed:', e);
                }
            } else {
                videoStarted = true;
            }

            nextContainer.classList.remove('hidden');
            nextContainer.classList.add('active');
            nextContainer.style.opacity = '0';
            nextContainer.style.transition = 'opacity 0.5s ease-in-out';
            currentContainer.style.transition = 'opacity 0.5s ease-in-out';

            const startFade = () => {
                currentContainer.classList.add('crossfade-out');
                nextContainer.classList.add('crossfade-in');

                requestAnimationFrame(() => {
                    currentContainer.style.opacity = '0';
                    currentContainer.classList.remove('active');
                    currentContainer.classList.add('hidden');
                    nextContainer.style.opacity = '1';
                });

                setTimeout(() => {
                    if (oldPlayer !== selectedPlayer && oldPlayerObj && typeof oldPlayerObj.stopVideo === 'function') {
                        try {
                            oldPlayerObj.stopVideo();
                        } catch (e) {
                            console.warn('stopVideo failed:', e);
                        }
                    }
                    currentContainer.classList.remove('crossfade-out');
                    nextContainer.classList.remove('crossfade-in');
                    this.sendPlayerSnapshot(oldPlayer);
                    this.sendPlayerSnapshot(selectedPlayer, 'playing');
                }, 500);
            };

            if (videoStarted) {
                setTimeout(startFade, wasPreloaded ? 50 : 300);
            } else {
                startFade();
            }
        } else {
            console.error('Container elements not found:', { oldPlayer, currentPlayer: selectedPlayer });
            const nextPlayerObj = this.players[selectedPlayer];
            if (nextPlayerObj && typeof nextPlayerObj.playVideo === 'function') {
                try {
                    nextPlayerObj.playVideo();
                } catch (e) {
                    console.warn('playVideo failed:', e);
                }
            }
            if (currentContainer) {
                currentContainer.style.opacity = '0';
                currentContainer.style.pointerEvents = 'none';
            }
            if (nextContainer) {
                nextContainer.style.opacity = '1';
                nextContainer.style.pointerEvents = 'auto';
            }
            setTimeout(() => {
                if (oldPlayer !== selectedPlayer && oldPlayerObj && typeof oldPlayerObj.stopVideo === 'function') {
                    try { oldPlayerObj.stopVideo(); } catch (e) { console.warn('stopVideo failed:', e); }
                }
            }, 500);
        }

        this.currentTrackInfo = this.playerTrackInfo[selectedPlayer] || this.nextTrackInfo;
        this.currentMediaInfo = this.playerMediaInfo[selectedPlayer] || this.nextMediaInfo;
        this.nextTrackInfo = null;
        this.nextMediaInfo = null;
        this.updateTrackInfoOverlay();
        this.sendFeedback('playing', videoId, selectedPlayer);
        console.log(`Switch complete. Current player: ${this.currentPlayer}`);
    }

    setPollingPort(port) {
        this.pollingPort = port;
        this.pollingUrl = `http://127.0.0.1:${port}/poll`;
        this.feedbackUrl = `http://127.0.0.1:${port}/feedback`;
        console.log(`Polling URL updated: ${this.pollingUrl}`);
    }

    // 状態フィードバック送信
    readPlayerTiming(playerId) {
        const targetPlayer = this.normalizePlayerId(playerId);
        const player = this.players[targetPlayer];
        let currentTime = null;
        let duration = null;
        try {
            if (player && typeof player.getCurrentTime === 'function') {
                const value = Number(player.getCurrentTime());
                if (Number.isFinite(value)) currentTime = value;
            }
            if (player && typeof player.getDuration === 'function') {
                const value = Number(player.getDuration());
                if (Number.isFinite(value) && value > 0) duration = value;
            }
        } catch (e) {
            // One-shot timing is optional; metadata duration remains available.
        }
        return { currentTime, duration };
    }

    async sendFeedback(state, videoId, playerId = null, options = {}) {
        try {
            const normalizedPlayer = playerId ? this.normalizePlayerId(playerId) : null;
            const feedbackData = {
                state: state,
                videoId: videoId || '',
                timestamp: Date.now(),
                currentPlayer: this.currentPlayer,
                nextPlayer: this.nextPlayer,
                sessionId: this.sessionId
            };

            if (normalizedPlayer) {
                feedbackData.playerId = normalizedPlayer;
                feedbackData.isCurrent = normalizedPlayer === this.currentPlayer;
                if (options.includeMetadata !== false) {
                    // Read the actual YouTube title only when state metadata is
                    // already being sent. HEARTBEAT explicitly skips this path.
                    const mediaInfo = this.refreshPlayerMediaInfoFromIframe(normalizedPlayer, videoId);
                    feedbackData.trackInfo = this.playerTrackInfo[normalizedPlayer] || null;
                    feedbackData.mediaInfo = mediaInfo || null;
                }
                if (options.includeTiming !== false) {
                    Object.assign(feedbackData, this.readPlayerTiming(normalizedPlayer));
                }
            }
            if (options.errorCode !== undefined) {
                feedbackData.errorCode = options.errorCode;
            }
            if (options.commandId) {
                feedbackData.commandId = options.commandId;
                feedbackData.commandStatus = options.commandStatus || 'accepted';
                if (options.commandError) feedbackData.commandError = options.commandError;
            }

            const response = await fetch(this.feedbackUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(feedbackData)
            });

            if (!response.ok) {
                console.error(`Failed to send feedback: ${response.status}`);
            }
        } catch (error) {
            console.error('Feedback error:', error.message);
            console.log('Feedback URL:', this.feedbackUrl);
        }
    }

    sendPlayerSnapshot(playerId, stateOverride = null) {
        const targetPlayer = this.normalizePlayerId(playerId);
        const state = stateOverride || this.playerStateName(targetPlayer);
        return this.sendFeedback(state, this.getVideoIdForPlayer(targetPlayer), targetPlayer);
    }

    sendAllPlayerSnapshots() {
        this.sendPlayerSnapshot('A');
        this.sendPlayerSnapshot('B');
    }

    // SET_CONFIG command handling.

    handleSetConfig(configJson) {
        try {
            const config = JSON.parse(configJson);
            console.log('Received SET_CONFIG:', config);

            if (config.trackInfoPosition !== undefined) {
                this.trackInfoPosition = config.trackInfoPosition;
                this.applyTrackInfoPosition();
                console.log(`Track info position updated to: ${this.trackInfoPosition}`);
            }
        } catch (e) {
            console.error('Error parsing SET_CONFIG:', e);
        }
    }

    // 楽曲情報オーバーレイの表示を更新
    updateTrackInfoOverlay() {
        const overlay = document.getElementById('trackInfoOverlay');
        const scrollContainer = document.getElementById('trackInfoScroll');
        const scrollText = document.getElementById('scrollText');
        const titleEl = document.getElementById('trackInfoTitle');
        const artistEl = document.getElementById('trackInfoArtist');
        const commentEl = document.getElementById('trackInfoComment');

        if (!overlay || !titleEl || !artistEl || !commentEl) {
            console.warn('Track info overlay elements not found');
            return;
        }

        // 表示位置が「表示しない」の場合は非表示
        if (this.trackInfoPosition === 'none') {
            overlay.classList.remove('visible');
            overlay.classList.add('hidden');
            if (scrollContainer) {
                scrollContainer.classList.remove('visible');
                scrollContainer.classList.add('hidden');
            }
            return;
        }

        const info = this.currentTrackInfo;

        // 楽曲情報がない場合（検索ボックスからの検索等）は非表示
        if (!info || (!info.title && !info.artist && !info.comment)) {
            overlay.classList.remove('visible');
            if (scrollContainer) {
                scrollContainer.classList.remove('visible');
            }
            return;
        }

        // 表示位置を適用
        this.applyTrackInfoPosition();

        if (this.trackInfoPosition === 'scroll') {
            if (scrollContainer && scrollText) {
                // テキストの構築
                const parts = [];
                if (info.title) parts.push(info.title);
                if (info.artist) parts.push(info.artist);
                if (info.comment) parts.push(info.comment);
                
                const fullText = parts.join('  -  ');
                scrollText.textContent = fullText;

                // アニメーションのリセット
                const prevAnim = scrollContainer.querySelector('.scroll-container');
                if (prevAnim) {
                    prevAnim.style.animation = 'none';
                    // reflow
                    prevAnim.offsetHeight;
                    prevAnim.style.animation = 'track-marquee 25s linear infinite';
                }

                scrollContainer.classList.remove('hidden');
                requestAnimationFrame(() => {
                    scrollContainer.classList.add('visible');
                });
            }
        } else {
            // 各行にテキストを設定（空の場合はCSSで非表示になる）
            titleEl.textContent = info.title || '';
            artistEl.textContent = info.artist || '';
            commentEl.textContent = info.comment || '';

            // hiddenクラスを除去してvisibleにする
            overlay.classList.remove('hidden');
            // 少し遅延してフェードイン（DOMの更新を確実に反映させるため）
            requestAnimationFrame(() => {
                overlay.classList.add('visible');
            });
        }

        console.log(`Track info displayed: ${info.title} / ${info.artist} / ${info.comment}`);
    }

    // 楽曲情報の表示位置を適用
    applyTrackInfoPosition() {
        const overlay = document.getElementById('trackInfoOverlay');
        const scrollContainer = document.getElementById('trackInfoScroll');
        if (!overlay) return;

        // 既存の位置クラスをすべて除去
        overlay.classList.remove(
            'track-info-top-right',
            'track-info-top-left',
            'track-info-bottom-right',
            'track-info-bottom-left',
            'hidden'
        );

        if (scrollContainer) {
            scrollContainer.classList.remove('hidden');
        }

        // 位置に応じたクラスを追加
        switch (this.trackInfoPosition) {
            case 'top-right':
                overlay.classList.add('track-info-top-right');
                if (scrollContainer) scrollContainer.classList.add('hidden');
                break;
            case 'top-left':
                overlay.classList.add('track-info-top-left');
                if (scrollContainer) scrollContainer.classList.add('hidden');
                break;
            case 'bottom-right':
                overlay.classList.add('track-info-bottom-right');
                if (scrollContainer) scrollContainer.classList.add('hidden');
                break;
            case 'bottom-left':
                overlay.classList.add('track-info-bottom-left');
                if (scrollContainer) scrollContainer.classList.add('hidden');
                break;
            case 'scroll':
                overlay.classList.add('hidden');
                if (scrollContainer) {
                    scrollContainer.classList.remove('hidden');
                }
                break;
            case 'none':
                overlay.classList.add('hidden');
                if (scrollContainer) scrollContainer.classList.add('hidden');
                overlay.classList.remove('visible');
                if (scrollContainer) scrollContainer.classList.remove('visible');
                break;
            default:
                overlay.classList.add('track-info-top-right');
                if (scrollContainer) scrollContainer.classList.add('hidden');
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
