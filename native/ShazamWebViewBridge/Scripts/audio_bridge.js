(() => {
  'use strict';
  if (window.top !== window || location.protocol !== 'https:' ||
      !['www.shazam.com', 'shazam.com'].includes(location.hostname)) return;
  if (window.__vjAudioBridge) return;
  const media = navigator.mediaDevices;
  const webview = window.chrome && window.chrome.webview;
  if (!media || !webview) return;

  let context = null;
  let cycle = '';
  let armed = false;
  let inputSampleRate = 0;
  let outputSampleRate = 0;
  let destinations = [];
  let processor = null;
  let clockSource = null;
  let segments = [];
  let segmentOffset = 0;
  let queuedSamples = 0;
  let playbackStarted = false;
  let totalPushed = 0;
  let totalConsumed = 0;
  let underflowSamples = 0;
  let firstDataReported = false;

  const MAX_QUEUE_SECONDS = 1.5;
  const PREBUFFER_SECONDS = 0.12;
  const PROCESSOR_FRAMES = 2048;

  const report = (type, extra = {}) => {
    try { webview.postMessage({source: 'VJShazam', type, id: cycle, ...extra}); }
    catch (_) {}
  };

  const resetQueue = () => {
    segments = [];
    segmentOffset = 0;
    queuedSamples = 0;
    playbackStarted = false;
    totalPushed = 0;
    totalConsumed = 0;
    underflowSamples = 0;
    firstDataReported = false;
  };

  const stop = () => {
    armed = false;
    if (clockSource) {
      try { clockSource.stop(); } catch (_) {}
      try { clockSource.disconnect(); } catch (_) {}
    }
    clockSource = null;
    if (processor) {
      try { processor.disconnect(); } catch (_) {}
      processor.onaudioprocess = null;
    }
    processor = null;
    for (const mediaDestination of destinations) {
      for (const track of mediaDestination.stream.getTracks()) {
        try { track.stop(); } catch (_) {}
      }
    }
    destinations = [];
    resetQueue();
    inputSampleRate = 0;
    outputSampleRate = 0;
    cycle = '';
    if (context) { context.close().catch(() => {}); context = null; }
  };

  const decodePcm16 = base64 => {
    if (typeof base64 !== 'string' || base64.length === 0 || base64.length > 400000)
      throw new Error('Invalid live PCM payload');
    const binary = atob(base64);
    if (binary.length === 0 || (binary.length & 1))
      throw new Error('Live PCM payload must be int16');
    const out = new Float32Array(binary.length >> 1);
    for (let i = 0, j = 0; i < binary.length; i += 2, j++) {
      let value = binary.charCodeAt(i) | (binary.charCodeAt(i + 1) << 8);
      if (value & 0x8000) value -= 0x10000;
      out[j] = value / 32768;
    }
    return out;
  };

  const resample = samples => {
    if (!samples.length || inputSampleRate === outputSampleRate) return samples;
    const ratio = outputSampleRate / inputSampleRate;
    const outLength = Math.max(1, Math.round(samples.length * ratio));
    const out = new Float32Array(outLength);
    for (let i = 0; i < outLength; i++) {
      const src = i / ratio;
      const left = Math.min(samples.length - 1, Math.floor(src));
      const right = Math.min(samples.length - 1, left + 1);
      const frac = src - left;
      out[i] = samples[left] + (samples[right] - samples[left]) * frac;
    }
    return out;
  };

  const dropOldestSegment = () => {
    if (!segments.length) return;
    const first = segments[0];
    const remaining = first.length - segmentOffset;
    queuedSamples = Math.max(0, queuedSamples - remaining);
    segments.shift();
    segmentOffset = 0;
  };

  const enqueue = samples => {
    if (!samples.length) return;
    segments.push(samples);
    queuedSamples += samples.length;
    totalPushed += samples.length;
    const maxSamples = Math.max(1, Math.floor(outputSampleRate * MAX_QUEUE_SECONDS));
    while (queuedSamples > maxSamples && segments.length > 1) dropOldestSegment();
  };

  const drainInto = output => {
    output.fill(0);
    const prebuffer = Math.floor(outputSampleRate * PREBUFFER_SECONDS);
    if (!playbackStarted) {
      if (queuedSamples < prebuffer) {
        underflowSamples += output.length;
        return;
      }
      playbackStarted = true;
    }

    let written = 0;
    while (written < output.length && segments.length) {
      const first = segments[0];
      const available = first.length - segmentOffset;
      const count = Math.min(available, output.length - written);
      output.set(first.subarray(segmentOffset, segmentOffset + count), written);
      written += count;
      segmentOffset += count;
      queuedSamples -= count;
      totalConsumed += count;
      if (segmentOffset >= first.length) {
        segments.shift();
        segmentOffset = 0;
      }
    }
    if (written < output.length) underflowSamples += output.length - written;
  };

  const ensureGenerator = () => {
    if (!context || processor) return;
    // Use one continuously-running Web Audio node instead of hundreds of short
    // AudioBufferSourceNodes. This avoids gaps caused by WebView2/IPC scheduling jitter.
    processor = context.createScriptProcessor(PROCESSOR_FRAMES, 1, 1);
    processor.onaudioprocess = event => {
      if (!armed) {
        event.outputBuffer.getChannelData(0).fill(0);
        return;
      }
      drainInto(event.outputBuffer.getChannelData(0));
    };
    clockSource = context.createConstantSource();
    clockSource.offset.value = 0;
    clockSource.connect(processor);
    clockSource.start();
  };

  const bridge = {
    async startLive(sampleRate, id) {
      stop();
      if (typeof id !== 'string' || !/^[a-f0-9]{32}$/.test(id))
        throw new Error('Invalid cycle ID');
      sampleRate = Number(sampleRate);
      if (!Number.isFinite(sampleRate) || sampleRate < 8000 || sampleRate > 96000)
        throw new Error('Invalid live PCM sample rate');
      // Keep the MediaStream at the same browser-friendly rate that the old reliable
      // WAV bridge used. Incoming capture PCM is resampled into this continuous stream.
      context = new AudioContext({sampleRate: 48000});
      cycle = id;
      inputSampleRate = Math.round(sampleRate);
      outputSampleRate = context.sampleRate;
      resetQueue();
      armed = true;
      report('audio-live-armed', {sampleRate: inputSampleRate, contextSampleRate: outputSampleRate});
      return {ok: true, sampleRate: inputSampleRate, contextSampleRate: outputSampleRate};
    },
    pushPcm16(base64, id) {
      if (!armed || !context || id !== cycle) return false;
      try {
        const decoded = decodePcm16(base64);
        let peak = 0;
        let sumSquares = 0;
        for (let i = 0; i < decoded.length; i++) {
          const a = Math.abs(decoded[i]);
          if (a > peak) peak = a;
          sumSquares += decoded[i] * decoded[i];
        }
        const rms = decoded.length ? Math.sqrt(sumSquares / decoded.length) : 0;
        const converted = resample(decoded);
        enqueue(converted);
        if (!firstDataReported) {
          firstDataReported = true;
          report('audio-live-data', {
            peak, rms,
            inputSampleRate,
            contextSampleRate: outputSampleRate,
            queuedMs: outputSampleRate ? Math.round(queuedSamples * 1000 / outputSampleRate) : 0
          });
        }
        return true;
      } catch (error) {
        report('audio-error', {error: String(error && error.message || error)});
        return false;
      }
    },
    stop,
    get cycle() { return cycle; },
    get armed() { return armed; }
  };

  const getAudio = async constraints => {
    // Never silently fall back to the OS default microphone. Python owns the selected input.
    if (!constraints || !constraints.audio || constraints.video)
      throw new DOMException('Only app-supplied audio is supported', 'NotSupportedError');
    if (!armed || !context)
      throw new DOMException('No active app live stream', 'NotReadableError');
    const current = context;
    await current.resume();
    if (current.state !== 'running') {
      report('audio-error', {error: 'AudioContext did not resume'});
      throw new DOMException('Audio context is suspended', 'NotReadableError');
    }
    // Shazam currently requests microphone access more than once per recognition.
    // The first returned track may be stopped immediately before the actual recognition
    // request. Never reuse that stopped track: return a fresh MediaStreamDestination for
    // every getUserMedia() call while keeping the PCM generator shared for this cycle.
    const mediaDestination = current.createMediaStreamDestination();
    mediaDestination.channelCount = 1;
    ensureGenerator();
    processor.connect(mediaDestination);
    destinations.push(mediaDestination);
    report('audio-stream-started', {
      context: current.state,
      mode: 'live-pcm-ring-fresh-track',
      queuedMs: outputSampleRate ? Math.round(queuedSamples * 1000 / outputSampleRate) : 0
    });
    const healthCycle = cycle;
    setTimeout(() => {
      if (!armed || cycle !== healthCycle || !outputSampleRate) return;
      report('audio-live-health', {
        queuedMs: Math.round(queuedSamples * 1000 / outputSampleRate),
        pushedMs: Math.round(totalPushed * 1000 / outputSampleRate),
        consumedMs: Math.round(totalConsumed * 1000 / outputSampleRate),
        underflowMs: Math.round(underflowSamples * 1000 / outputSampleRate)
      });
    }, 3000);
    return mediaDestination.stream;
  };

  webview.addEventListener('message', event => {
    const message = event && event.data;
    if (!message || message.source !== 'VJHost' || message.type !== 'audio-chunk') return;
    bridge.pushPcm16(message.pcm16Base64, message.id);
  });

  Object.defineProperty(media, 'getUserMedia', {
    configurable: true, writable: true, value: getAudio
  });
  for (const name of ['getUserMedia', 'webkitGetUserMedia']) {
    if (typeof navigator[name] === 'function') {
      navigator[name] = (constraints, success, failure) => getAudio(constraints).then(success, failure);
    }
  }
  Object.defineProperty(window, '__vjAudioBridge', {value: bridge, configurable: true});
})();
