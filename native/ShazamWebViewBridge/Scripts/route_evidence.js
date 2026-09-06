(() => {
  const baselineTrackIds = new Set(__BASELINE_TRACK_IDS__);
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = e => {
    if (!(e instanceof Element)) return false;
    const r = e.getBoundingClientRect();
    const st = getComputedStyle(e);
    return r.width > 2 && r.height > 2 && st.display !== 'none' &&
      st.visibility !== 'hidden' && Number(st.opacity || 1) > 0.01;
  };
  const generic = s => {
    s = clean(s).toLowerCase();
    if (!s) return true;
    if (new Set(['概要','overview','歌詞','lyrics','ビデオ','video','videos','ミュージックビデオ','music video',
      '関連','related','クレジット','credits','トップソング','top songs','アルバム','albums','おすすめ','featured',
      'フッター','footer','shazam フッター','shazam footer','ヘッダー','header','shazam ヘッダー','shazam header',
      'ナビゲーション','navigation','shazam ナビゲーション','shazam navigation']).has(s)) return true;
    return /ユーザーが今shazamで見つけている曲|今shazamで見つけている曲|shazamで見つけている曲|要求されたページは見つかりませんでした|page (?:was )?not found|music discovery|charts?\s*&\s*(song )?lyrics|音楽発見[、,]?\s*チャート\s*&\s*歌詞|音楽発見|shazam ホームページ|shazam homepage|^shazam\s*(?:フッター|footer|ヘッダー|header|ナビゲーション|navigation)$/.test(s);
  };
  const bad = s => {
    s = clean(s);
    return !s || s.length > 180 || generic(s) ||
      /name songs in seconds|find music|global top|featured|search for music|shazamで音楽|数秒で曲名|音楽を検索|世界トップ|チャートを見る/i.test(s);
  };
  const structuralUI = e => !!e?.closest?.('footer,[role="contentinfo"],nav,[role="navigation"],header');
  const appleId = href => {
    try {
      const u = new URL(href || '', location.href);
      if (!['music.apple.com', 'itunes.apple.com'].includes(u.hostname)) return '';
      const i = u.searchParams.get('i') || '';
      if (/^\d{6,20}$/.test(i)) return i;
      if (u.pathname.includes('/song/')) {
        const m = u.pathname.match(/\/song\/[^/?#]+\/(\d{6,20})(?:[/?#]|$)/i);
        if (m) return m[1];
      }
    } catch (_) {}
    return '';
  };
  const newAppleLink = (root = document) => {
    const links = [...root.querySelectorAll('a[href]')];
    const pick = links.find(a => {
      const id = appleId(a.href);
      return id && !baselineTrackIds.has(id) && visible(a);
    }) || links.find(a => {
      const id = appleId(a.href);
      return id && !baselineTrackIds.has(id);
    });
    return pick?.href || '';
  };
  const visibleArtist = (root = document) => {
    for (const e of root.querySelectorAll('a[href*="/artist/"], [data-testid*="artist" i], [data-testid*="subtitle" i]')) {
      const t = clean(e.innerText || e.textContent);
      if (visible(e) && !structuralUI(e) && t && t.length <= 140 && !generic(t)) return t;
    }
    return '';
  };
  const artistFromNode = node => {
    if (!node || typeof node !== 'object') return '';
    const by = node.byArtist || node.author || node.artist || node.creator;
    const one = x => {
      if (!x) return '';
      if (typeof x === 'string') return clean(x);
      if (typeof x === 'object') return clean(x.name || x.headline || '');
      return '';
    };
    return Array.isArray(by) ? by.map(one).filter(Boolean).join(', ') : one(by);
  };
  const findMusic = node => {
    if (!node || typeof node !== 'object') return null;
    if (Array.isArray(node)) {
      for (const x of node) { const y = findMusic(x); if (y) return y; }
      return null;
    }
    const types = [].concat(node['@type'] || []);
    if (types.some(x => typeof x === 'string' && /MusicRecording|Song/i.test(x))) {
      const title = clean(node.name || node.headline || '');
      if (title && !bad(title)) return {title, artist: artistFromNode(node)};
    }
    for (const v of Object.values(node)) { const y = findMusic(v); if (y) return y; }
    return null;
  };
  const route = (() => {
    try {
      const u = new URL(location.href);
      const m = u.pathname.match(/^\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?(song|track)\/(\d{6,20})(?:\/|$)/i);
      return m ? {kind: m[1].toLowerCase(), id: m[2], url: u.href} : null;
    } catch (_) { return null; }
  })();
  if (!route) return null;

  // Primary source: visible heading on the exact live recognition route.
  for (const h of document.querySelectorAll('h1,[data-testid*="title" i]')) {
    const title = clean(h.innerText || h.textContent);
    if (!visible(h) || structuralUI(h) || bad(title)) continue;
    let root = h.parentElement || h;
    let appleMusicUrl = '';
    for (let depth = 0; depth < 7 && root; depth++, root = root.parentElement) {
      appleMusicUrl = newAppleLink(root);
      if (appleMusicUrl) break;
    }
    const region = root || h.closest('article,section,[role="main"],main') || h.parentElement || document;
    return {
      title,
      artist: visibleArtist(region),
      appleTrackId: appleId(appleMusicUrl),
      appleMusicUrl,
      shazamTrackId: route.id,
      url: route.url,
      evidence: 'track-heading'
    };
  }

  // Secondary source: JSON-LD on the exact live route. Never attach a global
  // Apple link here because recommendation JSON-LD can coexist with the result.
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const item = findMusic(JSON.parse(s.textContent || 'null'));
      if (item) return {
        title: item.title,
        artist: item.artist || visibleArtist(),
        appleTrackId: '', appleMusicUrl: '',
        shazamTrackId: route.id, url: route.url, evidence: 'jsonld'
      };
    } catch (_) {}
  }

  return {
    title: '', artist: '', appleTrackId: '', appleMusicUrl: '',
    shazamTrackId: route.id, url: route.url, evidence: 'route-only'
  };
})()
