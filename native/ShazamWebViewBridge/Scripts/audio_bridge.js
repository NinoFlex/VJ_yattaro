(() => {
  'use strict';
  if (window.top !== window || location.protocol !== 'https:' ||
      !['www.shazam.com', 'shazam.com'].includes(location.hostname)) return;
  if (window.__vjAudioBridge) return;
  const media = navigator.mediaDevices;
  if (!media) return;
  let context = null;
  let buffer = null;
  let cycle = '';
  let armed = false;
  const live = new Set();

  const report = (type, extra = {}) => {
    try { window.chrome.webview.postMessage({source: 'VJShazam', type, id: cycle, ...extra}); }
    catch (_) {}
  };
  const stop = () => {
    armed = false;
    for (const item of live) {
      try { item.source.stop(); } catch (_) {}
      try { item.source.disconnect(); } catch (_) {}
      for (const track of item.destination.stream.getTracks()) track.stop();
    }
    live.clear();
    buffer = null;
    cycle = '';
    if (context) { context.close().catch(() => {}); context = null; }
  };

  const bridge = {
    async load(wavBase64, id) {
      stop();
      if (typeof id !== 'string' || !/^[a-f0-9]{32}$/.test(id))
        throw new Error('Invalid cycle ID');
      if (typeof wavBase64 !== 'string' || wavBase64.length > 2000000)
        throw new Error('Invalid audio payload');
      const bytes = Uint8Array.from(atob(wavBase64), c => c.charCodeAt(0));
      // Decode while suspended. Only the trusted recognition-button gesture resumes it.
      context = new AudioContext({sampleRate: 48000});
      buffer = await context.decodeAudioData(bytes.buffer);
      if (buffer.duration < 4.5 || buffer.duration > 21 || buffer.numberOfChannels !== 1) {
        stop();
        throw new Error('Expected a 5..20 second mono recording');
      }
      cycle = id;
      armed = true;
      report('audio-loaded', {duration: buffer.duration, sampleRate: buffer.sampleRate});
      return {ok: true, duration: buffer.duration};
    },
    stop,
    get cycle() { return cycle; },
    get armed() { return armed; }
  };

  const getAudio = async constraints => {
    // Never silently fall back to the OS default microphone. Python owns the selected input.
    if (!constraints || !constraints.audio || constraints.video)
      throw new DOMException('Only app-supplied audio is supported', 'NotSupportedError');
    if (!armed || !buffer || !context)
      throw new DOMException('No active app recording', 'NotReadableError');
    const current = context;
    await current.resume();
    if (current.state !== 'running') {
      report('audio-error', {error: 'AudioContext did not resume'});
      throw new DOMException('Audio context is suspended', 'NotReadableError');
    }
    const destination = current.createMediaStreamDestination();
    destination.channelCount = 1;
    const source = current.createBufferSource();
    source.buffer = buffer;
    // The website chooses its listening duration. Repeat the bounded snapshot only
    // inside this request, until it recognizes or times out. Nothing goes to speakers.
    source.loop = true;
    source.connect(destination);
    const item = {source, destination};
    live.add(item);
    for (const track of destination.stream.getTracks()) {
      track.addEventListener('ended', () => {
        try { source.stop(); } catch (_) {}
        source.disconnect();
        live.delete(item);
      });
    }
    source.start(current.currentTime + 0.02);
    report('audio-stream-started', {context: current.state});
    return destination.stream;
  };
  Object.defineProperty(media, 'getUserMedia', {
    configurable: true, writable: true, value: getAudio
  });
  // Older consumers may still call the legacy callback-based entrypoint.
  for (const name of ['getUserMedia', 'webkitGetUserMedia']) {
    if (typeof navigator[name] === 'function') {
      navigator[name] = (constraints, success, failure) => getAudio(constraints).then(success, failure);
    }
  }
  Object.defineProperty(window, '__vjAudioBridge', {value: bridge, configurable: true});
})();
