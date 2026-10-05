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
  const route = value => {
    if (typeof value !== 'string' || !value.trim()) return null;
    try {
      const u = new URL(value, location.href);
      if (!['www.shazam.com', 'shazam.com'].includes(u.hostname) || u.protocol !== 'https:') return null;
      const m = u.pathname.match(/^\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?(song|track)\/(\d{6,20})(?:\/|$)/i);
      return m ? {kind: m[1].toLowerCase(), id: m[2]} : null;
    } catch (_) { return null; }
  };
  const nodeText = e => clean(e?.innerText || e?.textContent);
  const unique = values => [...new Set(values.filter(Boolean))];
  const byArtist = item => {
    const names = [].concat(item.byArtist || []).map(value =>
      cleanArtist(typeof value === 'string' ? value : value?.name));
    return unique(names).join(', ');
  };
  const readRecording = (routeUrl) => {
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
        // Identity is mandatory: neither a matching heading nor a URL slug can
        // promote an unbound record into the current recognition result.
        if (title && exact)
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
  const appleId = value => {
    try {
      const u = new URL(value, location.href);
      if (u.protocol !== 'https:' || !['music.apple.com', 'itunes.apple.com'].includes(u.hostname)) return '';
      const id = u.searchParams.get('i') || '';
      if (/^\d{6,20}$/.test(id)) return id;
      const m = u.pathname.match(/\/song\/[^/]+\/(?:id)?(\d{6,20})\/?$/i);
      return m ? m[1] : '';
    } catch (_) { return ''; }
  };
  // These component prefixes and data-test-id attributes come from the two
  // supplied saved track pages. Do not depend on the generated CSS hash suffix.
  const ROOT_SELECTOR = '[class*="NewTrackPageHeader_trackContent__"]';
  const PRIMARY_TITLE_SELECTOR = '[class*="NewTrackPageHeader_trackTitle__"]';
  const PRIMARY_ARTIST_SELECTOR = 'a[data-test-id="track_userevent_artist_link"]';
  const PRIMARY_APPLE_SELECTOR = 'a[data-test-id="track_userevent_redirect_apple_music"]';
  const readPrimaryTrack = routeUrl => {
    const current = route(routeUrl);
    if (!current) return null;
    const identityOnly = {title: '', artist: '', shazamTrackId: current.id,
      appleTrackId: '', appleMusicUrl: '', url: routeUrl, evidence: 'route-only'};
    // During SPA navigation the URL may already be new while the DOM is old.
    const canonicals = [...document.querySelectorAll('link[rel="canonical"]')];
    if (canonicals.some(e => {
      const r = route(e.href);
      return !r || r.kind !== current.kind || r.id !== current.id;
    })) return identityOnly;
    const recording = readRecording(routeUrl);
    if (!recording) return identityOnly;
    const roots = [...document.querySelectorAll(ROOT_SELECTOR)].filter(e =>
      !e.closest('footer,nav,aside,dialog,[role="dialog"],[role="navigation"],[role="contentinfo"]') && visible(e));
    const valid = [];
    let conflictingHeader = false;
    for (const root of roots) {
      const titles = unique([...root.querySelectorAll(PRIMARY_TITLE_SELECTOR)].filter(visible).map(nodeText));
      if (titles.length !== 1 || fold(titles[0]) !== fold(recording.title)) {
        conflictingHeader = true;
        continue;
      }
      const names = unique([...root.querySelectorAll(PRIMARY_ARTIST_SELECTOR)]
        .filter(e => visible(e) && (() => {
          try {
            const u = new URL(e.href, routeUrl);
            return u.protocol === 'https:' && ['www.shazam.com','shazam.com'].includes(u.hostname)
              && /^\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?artist\//i.test(u.pathname);
          } catch (_) { return false; }
        })()).map(e => cleanArtist(nodeText(e))));
      if (!names.length) continue;
      const links = [...root.querySelectorAll(PRIMARY_APPLE_SELECTOR)].filter(e => visible(e) && appleId(e.href));
      const ids = unique(links.map(e => appleId(e.href)));
      const appleMusicUrl = ids.length === 1 ? links[0].href : '';
      valid.push({...identityOnly, title: titles[0], artist: names.join(', '),
        appleMusicUrl, appleTrackId: appleId(appleMusicUrl), evidence: 'track-primary-header'});
    }
    if (conflictingHeader) return identityOnly;
    const distinct = new Map(valid.map(x => [JSON.stringify([x.title,x.artist,x.appleTrackId]),x]));
    if (distinct.size > 1) return identityOnly;
    if (distinct.size === 1) return [...distinct.values()][0];
    // Only the exact primary MusicRecording; never citation/recommendation items.
    return {...identityOnly, title: recording.title, artist: recording.artist, evidence: 'jsonld'};
  };
  window.__vjTrackMetadata = {cleanArtist, visible, structural, key, route, readRecording, readPrimaryTrack};
})();
