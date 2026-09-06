(() => {
  const ids = [];
  for (const a of document.querySelectorAll('a[href]')) {
    try {
      const href = a.href || '';
      if (!/^https?:\/\/(?:music\.)?apple\.com\//i.test(href)) continue;
      const m = href.match(/[?&]i=(\d{6,20})(?:[&#]|$)/i);
      if (m) ids.push(m[1]);
    } catch (_) {}
  }
  return [...new Set(ids)];
})()
