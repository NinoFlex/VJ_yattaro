/* Shared, conservative readers for Shazam's recognized-track metadata.
 * No geometry-based guessing, wildcard subtitle selectors, or page-wide artist
 * fallback. Missing metadata stays missing and is resolved by the host later.
 */
(() => {
  if (!['www.shazam.com', 'shazam.com'].includes(location.hostname) || location.protocol !== 'https:') return;
  const clean = value => typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : '';
  const fold = value => clean(value).normalize('NFKC').toLowerCase().replace(/[\u200b-\u200d\ufeff]/g, '');
  const key = value => fold(value).replace(/[\s\p{P}\p{S}]+/gu, '');
  const promo = /apple\s*music\s*(?:\u306b|\u3067|\u3092|\u3068)|\u66f2\u5168\u4f53\u3092\u30d5\u30eb\u518d\u751f|\u958b\u767a\u8005\u5411\u3051\s*shazamkit|shazamkit\s+for\s+developers|(?:connect(?:\s+to)?|listen(?:\s+now)?\s+(?:on|in)|open\s+in|play\s+(?:on|in)|try|subscribe\s+to)\s+apple\s*music\b|connect\s+(?:with|to)\s+shazam|full\s+songs?\s+(?:in|on)\s+shazam/i;
  const ui = /^(?:apple\s*music|connect\s+apple\s*music|get\s+the\s+app|download\s+shazam|open\s+in\s+shazam|\u306b\u63a5\u7d9a|\u30a2\u30d7\u30ea\u3092\u5165\u624b|\u30a2\u30d7\u30ea\u3092\u30c0\u30a6\u30f3\u30ed\u30fc\u30c9|shazam\s+(?:footer|header|navigation)|\u6982\u8981|\u6b4c\u8a5e|\u5171\u6709|\u518d\u751f)$/i;
  const cleanArtist = value => {
    const text = clean(value), normalized = fold(text);
    return text && text.length <= 500 && !promo.test(normalized) && !ui.test(normalized) ? text : '';
  };
  const visible = e => {
    if (!e?.getBoundingClientRect) return false;
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 1 && r.height > 1 && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0.01;
  };
  const structural = e => !!e?.closest?.('footer,[role="contentinfo"],nav,[role="navigation"],header,aside,[role="complementary"],button,[role="button"]');
  const TITLE_SELECTOR = 'h1,[data-testid="track-title"],[data-testid="song-title"]';
  const HEADING_SELECTOR = 'h1,h2,h3,[data-testid="track-title"],[data-testid="song-title"]';
  const ARTIST_SELECTOR = '[data-testid="artist-name"],[data-testid="track-artist"],[data-testid="song-artist"],[data-testid="track-subtitle"],[data-testid="song-subtitle"],[itemprop="byArtist"]';
  const route = value => {
    if (typeof value !== 'string' || !value.trim()) return null;
    try {
      const u = new URL(value, location.href);
      if (!['www.shazam.com', 'shazam.com'].includes(u.hostname) || u.protocol !== 'https:') return null;
      const m = u.pathname.match(/^\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?(song|track)\/(\d{6,20})(?:\/|$)/i);
      return m ? {kind: m[1].toLowerCase(), id: m[2]} : null;
    } catch (_) { return null; }
  };
  const artistLink = e => {
    try {
      if (!e?.href) return false;
      const u = new URL(e.href, location.href);
      if (u.protocol !== 'https:') return false;
      if (['www.shazam.com', 'shazam.com'].includes(u.hostname))
        return /^\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?artist\//i.test(u.pathname);
      if (['music.apple.com', 'itunes.apple.com'].includes(u.hostname))
        return /^\/(?:[a-z]{2}\/)?artist\//i.test(u.pathname);
    } catch (_) {}
    return false;
  };
  const nodeText = e => clean(e?.innerText || e?.textContent);
  const unique = values => [...new Set(values.filter(Boolean))];
  const fieldText = e => {
    // A byArtist container may include artist URLs and other UI. Read only the
    // artist names in it, not the concatenated parent text.
    const names = [...e.querySelectorAll('[itemprop="name"],a[href]')]
      .filter(n => visible(n) && !structural(n) && (n.getAttribute?.('itemprop') === 'name' || artistLink(n)))
      .map(n => cleanArtist(nodeText(n)));
    return unique(names).join(', ') || cleanArtist(nodeText(e));
  };
  const artistNearTitle = title => {
    if (!visible(title) || structural(title)) return null;
    const wanted = key(nodeText(title));
    let root = title.parentElement;
    for (let depth = 0; root && depth < 4; depth++, root = root.parentElement) {
      // Never widen a failed lookup to the full page or its recommendation lists.
      if (root === document.body || root === document.documentElement || root.matches?.('main,[role="main"]')) break;
      if (structural(root) || !root.querySelectorAll) break;
      const others = [...root.querySelectorAll(HEADING_SELECTOR)].filter(e => visible(e) && !structural(e) && e !== title && key(nodeText(e)) !== wanted);
      if (others.length) break;
      const belongs = e => {
        if (!visible(e) || structural(e) || e === title || e.contains?.(title) || title.contains?.(e)) return false;
        const section = e.closest?.('section,article,[role="dialog"]');
        const titleSection = title.closest?.('section,article,[role="dialog"]');
        return !section || section === titleSection || section.contains?.(title);
      };
      const fields = [...root.querySelectorAll(ARTIST_SELECTOR)].filter(belongs);
      const topFields = fields.filter(e => !fields.some(other => other !== e && other.contains?.(e)));
      const names = unique(topFields.map(fieldText));
      if (names.length === 1) return {artist: names[0], region: root};
      if (names.length > 1) return null; // Conflicting metadata is not a guess.
      const links = [...root.querySelectorAll('a[href]')].filter(e => belongs(e) && artistLink(e));
      const linkedNames = unique(links.map(e => cleanArtist(nodeText(e))));
      if (linkedNames.length) return {artist: linkedNames.join(', '), region: root};
      if (root.matches?.('article,section,[role="dialog"]')) break;
    }
    return null;
  };
  const byArtist = item => {
    const names = [].concat(item.byArtist || []).map(value =>
      cleanArtist(typeof value === 'string' ? value : value?.name));
    return unique(names).join(', ');
  };
  const readRecording = (routeUrl, titleHint = '') => {
    const current = route(routeUrl);
    if (!current) return null;
    const results = [];
    const visit = (node, depth = 0) => {
      if (!node || typeof node !== 'object' || depth > 8 || results.length > 100) return;
      if (Array.isArray(node)) { node.slice(0, 100).forEach(x => visit(x, depth + 1)); return; }
      const types = [].concat(node['@type'] || []);
      if (types.some(t => typeof t === 'string' && /(?:^|[/#])(?:MusicRecording|Song)$/.test(t))) {
        const title = clean(node.name);
        const identities = [node.url, node['@id'], node.mainEntityOfPage].flatMap(x =>
          typeof x === 'string' ? [route(x)] : x && typeof x === 'object' ? [route(x['@id'] || x.url || '')] : []).filter(Boolean);
        const exact = identities.length && identities.every(x => x.kind === current.kind && x.id === current.id);
        // An explicit different ID is never repaired with text similarity. Without
        // an ID, require the primary record's title to match the actual title/slug.
        if (title && (identities.length ? exact : key(titleHint) && key(title) === key(titleHint)))
          results.push({title, artist: byArtist(node), exact: !!exact});
      }
      // Only primary structured records; do not recurse through recommendations,
      // ItemList, albums, author, creator, or every arbitrary object value.
      if (node['@graph']) visit(node['@graph'], depth + 1);
      if (node.mainEntity && typeof node.mainEntity === 'object') visit(node.mainEntity, depth + 1);
    };
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try { visit(JSON.parse(script.textContent || 'null')); } catch (_) {}
    }
    const exact = results.filter(x => x.exact);
    const pool = exact.length ? exact : results;
    const complete = pool.filter(x => x.artist);
    const choices = complete.length ? complete : pool;
    const distinct = new Map(choices.map(x => [JSON.stringify([x.title, x.artist]), x]));
    return distinct.size === 1 ? [...distinct.values()][0] : null;
  };
  window.__vjTrackMetadata = {cleanArtist, visible, structural, key, route, artistNearTitle, readRecording, TITLE_SELECTOR, HEADING_SELECTOR};
})();
