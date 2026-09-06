(() => {
  const isVisible = (e) => {
    if (!(e instanceof Element)) return false;
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    return r.width >= 24 && r.height >= 24 &&
           r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth &&
           s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0.01;
  };

  const collect = (root, out) => {
    try {
      for (const e of root.querySelectorAll('button,[role="button"],[aria-label],[data-testid*=shazam i],[id*=shazam i],[class*=shazam i]')) {
        out.push(e);
        if (e.shadowRoot) collect(e.shadowRoot, out);
      }
      for (const e of root.querySelectorAll('*')) {
        if (e.shadowRoot) collect(e.shadowRoot, out);
      }
    } catch (_) {}
  };

  const candidates = [];
  collect(document, candidates);
  let best = null;

  for (const e of candidates) {
    if (!isVisible(e)) continue;

    // The Shazam logo/home link also contains the word "Shazam". It is never a
    // recognition target, so reject normal links entirely.
    if (e.tagName === 'A' && e.getAttribute('href')) continue;

    const r = e.getBoundingClientRect();
    const topElement = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (topElement && !e.contains(topElement) && !topElement.contains(e)) continue;
    const aria = (e.getAttribute('aria-label') || '').toLowerCase();
    const title = (e.getAttribute('title') || '').toLowerCase();
    const testid = (e.getAttribute('data-testid') || '').toLowerCase();
    const id = (e.id || '').toLowerCase();
    const cls = (typeof e.className === 'string' ? e.className : '').toLowerCase();
    const text = (e.innerText || e.textContent || '').trim().toLowerCase();
    const joined = `${aria} ${title} ${testid} ${id} ${cls} ${text}`;

    if (/homepage|home page|go to shazam|shazam home|ホームページ|ホームへ|ホームに|トップページ|移動/.test(joined)) continue;

    let score = 0;
    const recognitionText = /identify|recognize|listen|name (a )?song|tap to shazam|click to shazam|曲を識別|曲名を調べ|音楽を認識|音楽認識|shazamする|曲を検索/.test(joined);
    if (recognitionText) score += 220;
    if (/shazam/.test(aria) && recognitionText) score += 90;
    if (/shazam/.test(title) && recognitionText) score += 60;
    if (/shazam/.test(testid + ' ' + id + ' ' + cls)) score += 50;
    if (text === 'shazam' && recognitionText) score += 40;

    const distRight = Math.abs(innerWidth - r.right);
    const distBottom = Math.abs(innerHeight - r.bottom);
    if (distRight < 180 && distBottom < 180) score += 70;
    if (r.width >= 36 && r.width <= 180 && r.height >= 36 && r.height <= 180) score += 20;
    if (Math.abs(r.width - r.height) < 24) score += 10;

    if (/search|menu|connect|apple music|play|pause|share|メニュー|接続|再生|一時停止|共有/.test(joined)) score -= 160;

    if (!best || score > best.score) {
      best = {
        score,
        x: r.left + r.width / 2,
        y: r.top + r.height / 2,
        label: (aria || title || text || testid || id).slice(0, 120)
      };
    }
  }

  // Weak "Shazam" matches are intentionally ignored. This prevents the homepage
  // logo from being clicked on the next recognition cycle.
  return best && best.score >= 180 ? best : null;
})()
