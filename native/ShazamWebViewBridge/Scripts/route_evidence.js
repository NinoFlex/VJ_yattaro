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
  const loose = s => clean(s).normalize('NFKC').toLowerCase()
    .replace(/[\s\p{P}\p{S}]+/gu, '');
  const artistNoise = s => {
    const t = clean(s);
    if (!t || t.length > 140 || generic(t)) return true;
    if (/^\d[\d,.]*$/.test(t)) return true;
    if (/^\d[\d,.]*\s*[•·・|/-]\s*(?:anime|pop|rock|j-?pop|hip[ -]?hop|soundtrack|dance|electronic|r&b|alternative|metal|country|classical|jazz|blues|latin|k-?pop)$/i.test(t)) return true;
    if (/^(?:anime|pop|rock|j-?pop|hip[ -]?hop|soundtrack|dance|electronic|r&b|alternative|metal|country|classical|jazz|blues|latin|k-?pop)$/i.test(t)) return true;
    if (/^(?:get the app|concerts|charts|radio spins|connect apple music|apple music|share|共有|再生|play|lyrics|歌詞|video|videos)$/i.test(t)) return true;
    return false;
  };
  const nearbyArtist = (titleElement, title) => {
    if (!titleElement || !visible(titleElement)) return '';
    const wanted = loose(title);
    const titleRect = titleElement.getBoundingClientRect();
    const candidates = [];
    let root = titleElement.parentElement || document;
    for (let depth = 0; root && depth < 6; depth++, root = root.parentElement) {
      if (!root.querySelectorAll) continue;
      for (const e of root.querySelectorAll('a,span,p,div')) {
        if (e === titleElement || titleElement.contains?.(e) || e.contains?.(titleElement)) continue;
        if (!visible(e) || structuralUI(e)) continue;
        const t = clean(e.innerText || e.textContent);
        if (artistNoise(t) || loose(t) === wanted || (wanted && loose(t).includes(wanted))) continue;
        const r = e.getBoundingClientRect();
        const gap = r.top - titleRect.bottom;
        if (gap < -8 || gap > 180) continue;
        if (r.right < titleRect.left - 80 || r.left > titleRect.right + 260) continue;
        const childPenalty = Math.min(6, e.children?.length || 0) * 80;
        const horizontal = Math.abs(r.left - titleRect.left) * 0.2;
        candidates.push({text: t, score: Math.max(0, gap) * 4 + childPenalty + horizontal});
      }
      if (candidates.length) break;
    }
    candidates.sort((a, b) => a.score - b.score || a.text.length - b.text.length);
    return candidates[0]?.text || '';
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
      const m = u.pathname.match(/^\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?(song|track)\/(\d{6,20})(?:\/([^/?#]+))?(?:\/|$)/i);
      if (!m) return null;
      let slugTitle = '';
      try { slugTitle = decodeURIComponent(m[3] || '').replace(/[-_]+/g, ' ').trim(); } catch (_) {}
      return {kind: m[1].toLowerCase(), id: m[2], url: u.href, slugTitle};
    } catch (_) { return null; }
  })();
  if (!route) return null;

  // Primary source: visible heading on the exact live recognition route. Do not
  // immediately return an incomplete heading: Shazam can paint the h1 before the
  // artist while JSON-LD already contains the complete track metadata.
  let headingCandidate = null;
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
    const explicitArtist = visibleArtist(region);
    const proximityArtist = explicitArtist ? '' : nearbyArtist(h, title);
    const candidate = {
      title,
      artist: explicitArtist || proximityArtist,
      appleTrackId: appleId(appleMusicUrl),
      appleMusicUrl,
      shazamTrackId: route.id,
      url: route.url,
      evidence: proximityArtist ? 'track-heading-nearby' : 'track-heading'
    };
    if (candidate.appleTrackId || candidate.artist) return candidate;
    if (!headingCandidate) headingCandidate = candidate;
  }

  // Secondary source: JSON-LD on the exact live route. Never attach a global
  // Apple link here because recommendation JSON-LD can coexist with the result.
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const item = findMusic(JSON.parse(s.textContent || 'null'));
      if (item) {
        const artist = item.artist || visibleArtist();
        const candidate = {
          title: item.title,
          artist,
          appleTrackId: '', appleMusicUrl: '',
          shazamTrackId: route.id, url: route.url, evidence: 'jsonld'
        };
        // Prefer complete JSON-LD over an h1 that has not acquired its artist yet.
        // If JSON-LD is also incomplete, retain the visible heading as the stronger
        // title source and let the next poll try again.
        if (artist || !headingCandidate) return candidate;
      }
    } catch (_) {}
  }

  // Some Shazam layouts render the title/artist as ordinary div/span text with
  // no artist link, data-testid or semantic heading. On an exact recognized route,
  // use the route slug only as a title anchor, then read the nearest visible line
  // below it as the artist. This is intentionally strict: the visible title must
  // match the route slug after punctuation/width normalization.
  if (route.slugTitle && !bad(route.slugTitle)) {
    const wanted = loose(route.slugTitle);
    const scanRoot = document.querySelector?.('main,[role="main"]') || document.body || document;
    for (const e of scanRoot.querySelectorAll('h1,h2,h3,div,span,p')) {
      if (!visible(e) || structuralUI(e)) continue;
      const displayedTitle = clean(e.innerText || e.textContent);
      if (!displayedTitle || loose(displayedTitle) !== wanted || bad(displayedTitle)) continue;
      const artist = nearbyArtist(e, displayedTitle);
      if (artist) {
        return {
          title: displayedTitle, artist,
          appleTrackId: '', appleMusicUrl: '',
          shazamTrackId: route.id, url: route.url, evidence: 'route-slug-dom'
        };
      }
    }
  }

  if (headingCandidate) return headingCandidate;

  return {
    title: '', artist: '', appleTrackId: '', appleMusicUrl: '',
    shazamTrackId: route.id, url: route.url, evidence: 'route-only'
  };
})()
