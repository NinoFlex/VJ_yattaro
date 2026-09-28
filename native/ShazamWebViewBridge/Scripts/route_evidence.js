(() => {
  const fields = window.__vjTrackMetadata;
  if (!fields) return null;
  const baselineTrackIds = new Set(__BASELINE_TRACK_IDS__);
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const {visible, structural: structuralUI, artistNearTitle, readRecording, key: loose} = fields;
  const generic = s => {
    s = clean(s).toLowerCase();
    if (!s) return true;
    if (new Set(['\u6982\u8981','overview','\u6b4c\u8a5e','lyrics','\u30d3\u30c7\u30aa','video','videos','\u30df\u30e5\u30fc\u30b8\u30c3\u30af\u30d3\u30c7\u30aa','music video',
      '\u95a2\u9023','related','\u30af\u30ec\u30b8\u30c3\u30c8','credits','\u30c8\u30c3\u30d7\u30bd\u30f3\u30b0','top songs','\u30a2\u30eb\u30d0\u30e0','albums','\u304a\u3059\u3059\u3081','featured',
      '\u30d5\u30c3\u30bf\u30fc','footer','shazam \u30d5\u30c3\u30bf\u30fc','shazam footer','\u30d8\u30c3\u30c0\u30fc','header','shazam \u30d8\u30c3\u30c0\u30fc','shazam header',
      '\u30ca\u30d3\u30b2\u30fc\u30b7\u30e7\u30f3','navigation','shazam \u30ca\u30d3\u30b2\u30fc\u30b7\u30e7\u30f3','shazam navigation']).has(s)) return true;
    return /\u30e6\u30fc\u30b6\u30fc\u304c\u4ecashazam\u3067\u898b\u3064\u3051\u3066\u3044\u308b\u66f2|\u4ecashazam\u3067\u898b\u3064\u3051\u3066\u3044\u308b\u66f2|shazam\u3067\u898b\u3064\u3051\u3066\u3044\u308b\u66f2|\u8981\u6c42\u3055\u308c\u305f\u30da\u30fc\u30b8\u306f\u898b\u3064\u304b\u308a\u307e\u305b\u3093\u3067\u3057\u305f|page (?:was )?not found|music discovery|charts?\s*&\s*(song )?lyrics|\u97f3\u697d\u767a\u898b|shazam \u30db\u30fc\u30e0\u30da\u30fc\u30b8|shazam homepage/.test(s);
  };
  const bad = s => !clean(s) || clean(s).length > 180 || generic(s) ||
    /name songs in seconds|find music|global top|featured|search for music|shazam\u3067\u97f3\u697d|\u6570\u79d2\u3067\u66f2\u540d|\u97f3\u697d\u3092\u691c\u7d22|\u4e16\u754c\u30c8\u30c3\u30d7|\u30c1\u30e3\u30fc\u30c8\u3092\u898b\u308b/i.test(s);
  const appleId = href => {
    try {
      const u = new URL(href || '', location.href);
      if (!['music.apple.com', 'itunes.apple.com'].includes(u.hostname)) return '';
      const i = u.searchParams.get('i') || '';
      if (/^\d{6,20}$/.test(i)) return i;
      const m = u.pathname.match(/\/song\/[^/?#]+\/(\d{6,20})(?:[/?#]|$)/i);
      return m ? m[1] : '';
    } catch (_) { return ''; }
  };
  const newAppleLink = root => [...root.querySelectorAll('a[href]')].find(e => {
    const id = appleId(e.href);
    return id && !baselineTrackIds.has(id) && visible(e) && !structuralUI(e);
  })?.href || '';
  const route = fields.route(location.href);
  if (!route) return null;
  let slugTitle = '';
  try {
    const m = new URL(location.href).pathname.match(/\/(?:song|track)\/\d{6,20}\/([^/?#]+)/i);
    slugTitle = decodeURIComponent(m?.[1] || '').replace(/[-_]+/g, ' ').trim();
  } catch (_) {}
  const candidate = (title, binding, evidence) => {
    const appleMusicUrl = binding ? newAppleLink(binding.region) : '';
    return {title, artist: binding?.artist || '', appleTrackId: appleId(appleMusicUrl), appleMusicUrl,
      shazamTrackId: route.id, url: location.href, evidence};
  };
  let headingCandidate = null;
  for (const h of document.querySelectorAll(fields.TITLE_SELECTOR)) {
    const title = clean(h.innerText || h.textContent);
    if (!visible(h) || structuralUI(h) || bad(title)) continue;
    const binding = artistNearTitle(h);
    const item = candidate(title, binding, 'track-heading');
    if (item.artist) return item;
    if (!headingCandidate) headingCandidate = item;
  }
  // The artist must be byArtist of the same primary MusicRecording, never an
  // unrelated artist link elsewhere on the page or a recommendation's metadata.
  const music = readRecording(location.href, headingCandidate?.title || slugTitle);
  if (music && !bad(music.title) && (music.artist || !headingCandidate))
    return {...candidate(music.title, null, 'jsonld'), artist: music.artist};
  if (slugTitle && !bad(slugTitle)) {
    const wanted = loose(slugTitle);
    const scanRoot = document.querySelector?.('main,[role="main"]') || document.body || document;
    for (const e of scanRoot.querySelectorAll('h1,h2,h3,div,span,p')) {
      if (!visible(e) || structuralUI(e)) continue;
      const title = clean(e.innerText || e.textContent);
      if (bad(title) || !wanted || loose(title) !== wanted) continue;
      const binding = artistNearTitle(e);
      // A matching URL slug anchors only the title. It does not make arbitrary
      // adjacent text into an artist. Keep this source distinct from the old heuristic.
      if (binding) return candidate(title, binding, 'route-slug-artist');
    }
  }
  return headingCandidate || candidate('', null, 'route-only');
})();
