(() => {
  'use strict';
  if (window.top !== window || location.protocol !== 'https:' ||
      !['www.shazam.com', 'shazam.com'].includes(location.hostname)) return;
  if (window.__vjAudioRetry) return;

  // Substituted by BridgeHost before document injection. Keep initial and
  // retry targeting on exactly the same production selector implementation.
  const findButton = () => __VJ_FIND_BUTTON__;
  let guard = null;
  let lastOutcome = null;
  const safe = (fn, fallback = null) => { try { return fn(); } catch (_) { return fallback; } };
  const visible = e => {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 &&
      r.top < innerHeight && r.left < innerWidth && s.display !== 'none' &&
      s.visibility !== 'hidden' && Number(s.opacity || 1) > 0.01;
  };
  const screenBusy = () => {
    const nodes = [...document.querySelectorAll(
      '[role="dialog"],[aria-modal="true"],dialog[open],[role="status"],[aria-live],[role="progressbar"]')];
    // Do not scan/log the document text or infer idleness from a missing spinner.
    if (nodes.length > 100) return true; // Too ambiguous to prove an idle screen.
    return nodes.some(e => visible(e) && (
      e.matches('[role="dialog"],[aria-modal="true"],dialog[open],[role="progressbar"]') ||
      /\b(listening|recognizing|recognising|identifying|recording|processing|loading)\b|\u8a8d\u8b58\u4e2d|\u8b58\u5225\u4e2d|\u9332\u97f3\u4e2d|\u691c\u7d22\u4e2d|\u8aad\u307f\u8fbc\u307f\u4e2d/i.test((e.textContent || '').slice(0, 400))));
  };

  const probe = (id, home, key, duringClick = false) => {
    const deny = (reason, extra = {}) => ({allowed: false, reason, ...extra});
    if (!/^[a-f0-9]{32}$/.test(id || '')) return deny('invalid-cycle');
    const expected = safe(() => new URL(home));
    if (!expected || location.origin !== expected.origin ||
        location.pathname.replace(/\/$/, '') !== expected.pathname.replace(/\/$/, ''))
      return deny('not-home');
    if (document.readyState !== 'complete' || !document.body) return deny('page-not-complete');
    const h = safe(() => window.__vjAudioBridge?.getDiagnostics?.());
    if (!h || h.cycle !== id || !h.armed || !h.mediaHookInstalled)
      return deny('bridge-or-cycle-changed');
    if (h.getUserMediaCalls !== 0 || h.getUserMediaSuccesses !== 0 || h.getUserMediaFailures !== 0)
      return deny('audio-already-requested', {calls: h.getUserMediaCalls});
    if (!['running', 'suspended'].includes(h.contextState) || !h.firstDataReported || h.pcmChunksReceived < 1)
      return deny('audio-not-ready');
    if (!Number.isInteger(h.clickEvents) || h.clickEvents < 0 || h.clickEvents > (duringClick ? 2 : 1))
      return deny('unexpected-click-count');
    if (screenBusy()) return deny('busy-or-modal');
    const button = safe(findButton);
    if (!button) return deny('button-unavailable');
    if (button.key !== key) return deny('button-changed');
    if (button.idleConfirmed !== true) return deny('idle-not-proven', {button});
    return {allowed: true, reason: button.stateReason, button, calls: 0, clicks: h.clickEvents};
  };

  const finish = id => {
    if (guard && guard.id === id) {
      if (!lastOutcome || lastOutcome.id !== id)
        lastOutcome = {id, outcome: 'not-delivered', reason: 'no-click-event'};
      guard = null;
    }
    return lastOutcome && lastOutcome.id === id ? lastOutcome : null;
  };
  const arm = (id, home, key) => {
    const result = probe(id, home, key);
    if (!result.allowed) return result;
    if (guard) return {allowed: false, reason: 'guard-already-armed'};
    lastOutcome = null;
    guard = {id, home, key, expires: performance.now() + 2000, blocked: '', delivered: false};
    return result;
  };
  const describeTarget = (event, button) => {
    const atCenter = document.elementFromPoint(button.x, button.y);
    const control = atCenter && atCenter.closest('button,[role="button"]');
    return control && event.target instanceof Element && control.contains(event.target);
  };

  // A C# snapshot alone is insufficient: getUserMedia or a DOM transition may
  // occur before CDP delivers the next mouse event. Revalidate synchronously in
  // capture phase. This guard exists ONLY for the one explicitly armed retry.
  // An expired guard blocks rather than allowing a delayed CDP click through.
  const onPointer = event => {
    if (!guard || (event.button !== undefined && event.button !== 0)) return;
    const g = guard;
    const state = safe(() => probe(g.id, g.home, g.key, event.type === 'click'),
      {allowed: false, reason: 'probe-failed'});
    let reason = g.blocked || (g.delivered ? 'retry-already-delivered' : '') ||
      (performance.now() > g.expires ? 'guard-expired' : '');
    if (!reason && !state.allowed) reason = state.reason;
    if (!reason && !safe(() => describeTarget(event, state.button), false)) reason = 'click-target-changed';
    if (reason) {
      g.blocked = reason;
      event.preventDefault();
      event.stopImmediatePropagation();
      lastOutcome = {id: g.id, outcome: 'blocked', reason};
    } else if (event.type === 'click') {
      g.delivered = true;
      lastOutcome = {id: g.id, outcome: 'delivered', reason: state.reason};
    }
    // Keep the guard until the host has observed the dispatch result. Clearing
    // on pointerup would allow a later click to toggle an in-progress session.
  };
  for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'])
    document.addEventListener(type, onPointer, true);
  Object.defineProperty(window, '__vjAudioRetry', {value: {
    probe: (id, home, key) => safe(() => probe(id, home, key), {allowed: false, reason: 'probe-failed'}),
    arm: (id, home, key) => safe(() => arm(id, home, key), {allowed: false, reason: 'probe-failed'}),
    finish,
    cancel: id => { if (guard?.id === id) {
      guard.blocked = 'host-cancelled';
      lastOutcome = {id, outcome: 'blocked', reason: 'host-cancelled'};
    } },
    getDiagnostics: () => ({armed: !!guard, id: guard?.id || '',
      blocked: guard?.blocked || '', lastOutcome})
  }, configurable: true});
})();
