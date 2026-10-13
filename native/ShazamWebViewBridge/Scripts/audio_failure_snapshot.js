// Evaluated only after an audio-request timeout. The bridge is read-only here;
// no site click, microphone request or navigation is initiated by this probe.
(() => {
  const safely = (fn, fallback = null) => {
    try { return fn(); } catch (_) { return fallback; }
  };
  const target = safely(() => document.elementFromPoint(__VJ_CLICK_X__, __VJ_CLICK_Y__));
  const element = node => !node ? null : {
    tag: String(node.tagName || '').slice(0, 32),
    id: String(node.id || '').slice(0, 64),
    aria: String(node.getAttribute?.('aria-label') || '').slice(0, 90),
    testId: String(node.getAttribute?.('data-testid') || '').slice(0, 64)
  };
  return {
    path: safely(() => location.pathname.slice(0, 200), ''),
    readyState: safely(() => document.readyState, ''),
    visibility: safely(() => document.visibilityState, ''),
    hasFocus: safely(() => document.hasFocus(), false),
    iframeCount: safely(() => document.querySelectorAll('iframe').length, -1),
    clickPointTarget: element(target),
    bridgeAvailable: !!window.__vjAudioBridge,
    bridge: safely(() => window.__vjAudioBridge?.getDiagnostics?.())
  };
})()
