(() => {
  const isVisible = (e, minimum = 24) => {
    if (!(e instanceof Element)) return false;
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    return r.width >= minimum && r.height >= minimum &&
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

  const rawCandidates = [];
  collect(document, rawCandidates);
  // Inspect the actionable parent, never the inner SVG/logo or an arbitrary
  // container. Returning a coordinate does not mean the site is hydrated yet.
  const candidates = [...new Set(rawCandidates.map(e =>
    e.closest('button,[role="button"]')).filter(Boolean))];
  let best = null;

  for (const e of candidates) {
    if (!isVisible(e)) continue;

    // The Shazam logo/home link also contains the word "Shazam". It is never a
    // recognition target, so reject normal links entirely.
    if (e.closest('a[href]')) continue;
    if (e.matches(':disabled') || e.closest('[inert],[hidden],[aria-disabled="true"],[aria-busy="true"]')) continue;
    if (getComputedStyle(e).pointerEvents === 'none') continue;

    const r = e.getBoundingClientRect();
    const topElement = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!topElement || !e.contains(topElement)) continue;
    const aria = (e.getAttribute('aria-label') || '').toLowerCase();
    const title = (e.getAttribute('title') || '').toLowerCase();
    const testid = (e.getAttribute('data-testid') || '').toLowerCase();
    const id = (e.id || '').toLowerCase();
    const cls = (typeof e.className === 'string' ? e.className : '').toLowerCase();
    const text = (e.innerText || e.textContent || '').trim().toLowerCase();
    const joined = `${aria} ${title} ${testid} ${id} ${cls} ${text}`;

    if (/homepage|home page|go to shazam|shazam home|ホームページ|ホームへ|ホームに|トップページ|移動/.test(joined)) continue;

    const state = (e.getAttribute('data-recognition-state') ||
      e.getAttribute('data-state') || e.getAttribute('data-status') || '').trim().toLowerCase();
    const busyText = /\b(listening|recognizing|recognising|identifying|recording|processing|loading|cancel|stop)\b|\u8a8d\u8b58\u4e2d|\u8b58\u5225\u4e2d|\u691c\u7d22\u4e2d|\u9332\u97f3\u4e2d|\u30ad\u30e3\u30f3\u30bb\u30eb|\u505c\u6b62|\u8aad\u307f\u8fbc\u307f\u4e2d/;
    const hasProgress = [...e.querySelectorAll('[role="progressbar"],[aria-busy="true"],[data-state="loading"]')]
      .some(child => isVisible(child, 1));
    if (e.getAttribute('aria-pressed') === 'true' || busyText.test(joined + ' ' + state) || hasProgress) continue;
    const idleAction = /\b(?:tap|click) to shazam\b|\b(?:tap|click) to (?:identify|recognize)\b|(?:\u30bf\u30c3\u30d7|\u30af\u30ea\u30c3\u30af)\u3057\u3066\s*(?:shazam|\u66f2\u3092\u8b58\u5225|\u97f3\u697d\u3092\u8a8d\u8b58)/;
    // A contradictory/unknown explicit state must not be overridden by a
    // stale "Tap to Shazam" label while the application is changing state.
    const idleConfirmed = state ? state === 'idle' : idleAction.test(aria + ' ' + title + ' ' + text);
    const stateReason = idleConfirmed ? (state ? 'explicit-idle-state' : 'explicit-idle-action') : 'idle-not-proven';

    let score = 0;
    const recognitionText = /identify|recognize|music recognition|recognition button|listen|name (a )?song|tap to shazam|click to shazam|曲を識別|曲名を調べ|音楽を認識|音楽認識|shazamする|曲を検索/.test(joined);
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
        label: (aria || title || text || testid || id).slice(0, 120),
        key: JSON.stringify([e.tagName, id.slice(0, 80), testid.slice(0, 80), aria.slice(0, 120), title.slice(0, 120)]),
        idleConfirmed,
        stateReason
      };
    }
  }

  // Weak "Shazam" matches are intentionally ignored. This prevents the homepage
  // logo from being clicked on the next recognition cycle.
  return best && best.score >= 180 ? best : null;
})()
