(() => {
  'use strict';
  if (window.top !== window || location.protocol !== 'https:' ||
      !['www.shazam.com', 'shazam.com'].includes(location.hostname)) return;
  if (window.__vjResults) return;
  let cycle = '';
  let lastKey = '';
  let latest = null;
  let knownDialogs = new Set();
  let knownPairs = new Set();
  let noMatchSent = false;
  const badText = /requested page|page (?:was )?not found|music discovery|\u8981\u6c42\u3055\u308c\u305f\u30da\u30fc\u30b8\u306f\u898b\u3064\u304b\u308a\u307e\u305b\u3093|\u30e6\u30fc\u30b6\u30fc\u304c\u4ecaShazam\u3067\u898b\u3064\u3051\u3066\u3044\u308b\u66f2|\u97f3\u697d\u767a\u898b\u3001\u30c1\u30e3\u30fc\u30c8/i;
  const uiTitleLabels = new Set(['概要','overview','歌詞','lyrics','ビデオ','video','videos',
    'ミュージックビデオ','music video','関連','related','クレジット','credits',
    'トップソング','top songs','アルバム','albums','おすすめ','featured',
    'フッター','footer','shazam フッター','shazam footer','ヘッダー','header','shazam ヘッダー','shazam header',
    'ナビゲーション','navigation','shazam ナビゲーション','shazam navigation']);
  const failurePatterns = [
    /曲を認識できませんでした/i,
    /曲が見つかりませんでした/i,
    /一致する曲が見つかりません/i,
    /音楽を認識できませんでした/i,
    /could(?:n't| not) recognize/i,
    /could(?:n't| not) find (?:a )?(?:song|match)/i,
    /no (?:song|match) (?:was )?found/i
  ];
  const clean = v => {
    if (typeof v !== 'string') return '';
    const s = v.replace(/\s+/g, ' ').trim();
    return s.length <= 500 && !badText.test(s) ? s : '';
  };
  const cleanTitle = v => {
    const s = clean(v);
    if (!s) return '';
    const folded = s.toLowerCase();
    if (uiTitleLabels.has(folded)) return '';
    if (/^shazam\s*(?:フッター|footer|ヘッダー|header|ナビゲーション|navigation)$/i.test(s)) return '';
    return s;
  };
  const appleId = value => {
    try {
      const u = new URL(value, location.href);
      if (!['music.apple.com', 'itunes.apple.com'].includes(u.hostname)) return '';
      const i = u.searchParams.get('i') || '';
      if (/^\d{6,20}$/.test(i)) return i;
      if (u.pathname.includes('/song/')) {
        const m = u.pathname.match(/\/(?:id)?(\d{6,20})\/?$/);
        if (m) return m[1];
      }
    } catch (_) {}
    return '';
  };
  const route = value => {
    try {
      const u = new URL(value, location.href);
      if (!['www.shazam.com', 'shazam.com'].includes(u.hostname) || u.protocol !== 'https:') return null;
      const m = u.pathname.match(/^\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?(song|track)\/(\d{6,20})(?:\/|$)/i);
      if (!m) return null;
      // Legacy /track IDs are Shazam IDs, NOT Apple IDs.
      return {kind: m[1].toLowerCase(), id: m[2], url: u.href};
    } catch (_) { return null; }
  };
  const publish = (candidate, id = cycle) => {
    if (!id || id !== cycle) return;
    candidate.title = cleanTitle(candidate.title);
    candidate.artist = clean(candidate.artist);
    candidate.appleTrackId = /^\d{6,20}$/.test(candidate.appleTrackId || '') ? candidate.appleTrackId : '';
    candidate.shazamTrackId = /^\d{6,20}$/.test(candidate.shazamTrackId || '') ? candidate.shazamTrackId : '';
    if (!candidate.appleTrackId && !candidate.shazamTrackId && !(candidate.title && candidate.artist)) return;
    const key = JSON.stringify(candidate);
    if (key === lastKey) return;
    lastKey = key;
    latest = candidate;
    window.chrome?.webview?.postMessage({source: 'VJShazam', type: 'candidate', id, ...candidate});
  };
  const findAppleLink = object => {
    let budget = 300;
    const walk = (v, depth) => {
      if (--budget < 0 || depth > 8 || v == null) return '';
      if (typeof v === 'string') return appleId(v) ? v : '';
      if (typeof v !== 'object') return '';
      for (const item of Object.values(v)) { const found = walk(item, depth + 1); if (found) return found; }
      return '';
    };
    return walk(object, 0);
  };

  const inspectRecognition = (payload, id) => {
    if (!id || id !== cycle || !payload || typeof payload !== 'object') return;
    // Observe ONLY a completed recognition response. Do not treat homepage charts,
    // recommendations, arbitrary catalog JSON, or a /track page as recognition.
    const candidates = [payload, payload.data, payload.result].filter(Boolean);
    for (const p of candidates) {
      if (!Array.isArray(p.matches)) continue;
      // A genuine Shazam recognition response with an empty matches array is a
      // definitive no-match.  Signal it immediately so the host can retry the
      // newest recording instead of waiting for the long safety deadline.
      const looksRecognitionResponse = Object.prototype.hasOwnProperty.call(p, 'matches') &&
        ('timestamp' in p || 'timezone' in p || 'tagid' in p || 'track' in p);
      if (p.matches.length === 0 && !p.track && looksRecognitionResponse) {
        if (!noMatchSent && id === cycle) {
          noMatchSent = true;
          window.chrome?.webview?.postMessage({source: 'VJShazam', type: 'no-match', id});
        }
        continue;
      }
      if (p.matches.length === 0 || !p.track) continue;
      const t = p.track;
      const title = cleanTitle(t.title), artist = clean(t.subtitle || t.artist);
      if (!title || !artist) continue;
      const appleMusicUrl = findAppleLink(t);
      const shazamRoute = route(t.url || '');
      publish({title, artist, appleMusicUrl,
        appleTrackId: appleId(appleMusicUrl),
        shazamTrackId: shazamRoute?.id || '',
        url: String(t.url || location.href), evidence: 'recognition-response'}, id);
    }
  };
  const originalFetch = window.fetch;
  if (typeof originalFetch === 'function') {
    window.fetch = async function(...args) {
      const id = cycle; // Request-scoped: late responses cannot belong to the next recording.
      const response = await originalFetch.apply(this, args);
      if (id && /json/i.test(response.headers.get('content-type') || '')) {
        response.clone().text().then(text => {
          if (text.length <= 2000000) {
            try { inspectRecognition(JSON.parse(text), id); } catch (_) {}
          }
        }).catch(() => {});
      }
      return response;
    };
  }
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function(...args) {
    const id = cycle;
    if (id) this.addEventListener('load', () => {
      try {
        if (this.responseType === 'json') inspectRecognition(this.response, id);
        else if ((!this.responseType || this.responseType === 'text') && this.responseText.length <= 2000000)
          inspectRecognition(JSON.parse(this.responseText), id);
      } catch (_) {}
    }, {once: true});
    return originalSend.apply(this, args);
  };

  const inspectRoute = value => {
    if (!cycle) return;
    const r = route(value);
    if (!r) return;
    // Capture the current result card before the site's SPA replaces it with a
    // detail/404 page. If text was found, bind that text to this exact /song ID.
    scanDOM();
    if (latest && latest.title && latest.artist) {
      publish({...latest, shazamTrackId: r.id, url: r.url, evidence: latest.evidence + '+shazam-route'});
    } else {
      publish({title: '', artist: '', shazamTrackId: r.id, appleTrackId: '', appleMusicUrl: '',
        url: r.url, evidence: 'shazam-route'});
    }
  };
  for (const name of ['pushState', 'replaceState']) {
    const original = history[name];
    history[name] = function(state, title, url) {
      if (url) inspectRoute(url);
      return original.apply(this, arguments); // Do not corrupt the site's router state.
    };
  }
  addEventListener('popstate', () => inspectRoute(location.href));

  const visible = element => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 1 && rect.height > 1 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const structuralUI = element => {
    if (!element?.closest) return false;
    return !!element.closest('footer,[role="contentinfo"],nav,[role="navigation"],header');
  };
  const readRegion = region => {
    if (!region?.querySelector || !region?.querySelectorAll || structuralUI(region)) return null;
    const titleNode = region.querySelector('h1,h2,[data-testid="track-title"],[data-testid="song-title"]');
    const artistNode = region.querySelector('a[href*="/artist/"],[data-testid="artist-name"],[data-testid="track-subtitle"]');
    if (structuralUI(titleNode) || structuralUI(artistNode)) return null;
    const title = cleanTitle(titleNode?.textContent), artist = clean(artistNode?.textContent);
    const links = Array.from(region.querySelectorAll('a[href]'));
    const appleMusicUrl = links.map(a => a.href).find(h => appleId(h)) || '';
    if (title && artist) {
      const candidate = {title, artist, appleMusicUrl, appleTrackId: appleId(appleMusicUrl),
        url: location.href, evidence: 'result-region'};
      publish(candidate);
      return candidate;
    }
    return null;
  };

  const pairFromHeading = heading => {
    if (!heading || !visible(heading) || structuralUI(heading)) return null;
    const title = cleanTitle(heading.textContent);
    if (!title) return null;
    let region = heading;
    for (let depth = 0; region && depth < 6; depth++, region = region.parentElement) {
      if (!region.querySelector || !region.querySelectorAll) continue;
      const artistNode = region.querySelector('a[href*="/artist/"],[data-testid*="artist" i],[data-testid*="subtitle" i]');
      if (structuralUI(artistNode)) continue;
      const artist = clean(artistNode?.textContent);
      if (!artist) continue;
      const links = Array.from(region.querySelectorAll('a[href]'));
      const appleMusicUrl = links.map(a => a.href).find(h => appleId(h)) || '';
      return {title, artist, appleMusicUrl, appleTrackId: appleId(appleMusicUrl),
        url: location.href, evidence: 'new-result-heading'};
    }
    return null;
  };

  const visiblePairs = () => {
    const result = [];
    if (!document.querySelectorAll) return result;
    for (const h of document.querySelectorAll('h1,h2,h3,[data-testid*="title" i]')) {
      const candidate = pairFromHeading(h);
      if (candidate) result.push(candidate);
    }
    return result;
  };
  const pairKey = c => `${c.title}\u001f${c.artist}`.toLowerCase();
  const scanNewPairs = () => {
    for (const candidate of visiblePairs()) {
      const key = pairKey(candidate);
      if (knownPairs.has(key)) continue;
      knownPairs.add(key);
      publish(candidate);
    }
  };
  const scanFailure = () => {
    if (!cycle || noMatchSent || route(location.href)) return;
    const selectors = '[role="alert"],[role="status"],[aria-live],dialog,[role="dialog"],h1,h2,h3,p';
    for (const e of document.querySelectorAll(selectors)) {
      if (!visible(e)) continue;
      const text = (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim();
      if (!text || text.length > 240) continue;
      if (failurePatterns.some(p => p.test(text))) {
        noMatchSent = true;
        window.chrome?.webview?.postMessage({source: 'VJShazam', type: 'no-match', id: cycle, reason: text.slice(0, 220)});
        return;
      }
    }
  };
  function scanDOM() {
    if (!cycle || !document.body) return;
    const r = route(location.href);
    if (r) {
      // JSON-LD is accepted only on a recognized song/track route, never on home.
      for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
        try {
          const raw = JSON.parse(script.textContent);
          const values = Array.isArray(raw) ? raw : (raw['@graph'] || [raw]);
          for (const item of values) {
            const types = [].concat(item['@type'] || []);
            if (!types.includes('MusicRecording')) continue;
            const artist = [].concat(item.byArtist || []).map(a => a?.name || '').filter(Boolean).join(', ');
            if (cleanTitle(item.name) && clean(artist)) publish({title: item.name, artist,
              shazamTrackId: r.id, appleTrackId: '', appleMusicUrl: '',
              url: location.href, evidence: 'jsonld'});
          }
        } catch (_) {}
      }
      readRegion(document.querySelector('main') || document.body);
    } else {
      // Prefer newly shown result dialogs, then accept only title+artist pairs that
      // were not visible when this recognition cycle was armed. This catches the
      // transient Shazam result card even when it is not marked role=dialog.
      for (const region of document.querySelectorAll('[role="dialog"],dialog,[aria-modal="true"]'))
        if (visible(region) && !knownDialogs.has(region)) readRegion(region);
      scanNewPairs();
      scanFailure();
    }
  }
  let pending = false;
  const observer = new MutationObserver(() => {
    if (!cycle || pending) return;
    pending = true;
    queueMicrotask(() => { pending = false; scanDOM(); });
  });
  observer.observe(document, {subtree: true, childList: true, characterData: true});
  window.__vjResults = {
    arm(id) {
      cycle = id; lastKey = ''; latest = null; noMatchSent = false;
      knownDialogs = new Set(Array.from(document.querySelectorAll('[role="dialog"],dialog,[aria-modal="true"]')).filter(visible));
      knownPairs = new Set(visiblePairs().map(pairKey));
      return true;
    },
    stop() { cycle = ''; latest = null; lastKey = ''; noMatchSent = false; },
    scan() { scanDOM(); return latest; },
    // Exposed only for local fixture tests; there is no network call here.
    inspectRecognition,
    appleId,
    route
  };
})();
