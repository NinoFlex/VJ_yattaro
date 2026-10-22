/* Offline YouTube PRELOAD-retry checks. No browser or network required. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync(path.join(__dirname, '..', 'web', 'player.js'), 'utf8');
let passed = 0;
function check(label, test) { test(); passed++; console.log('PASS:', label); }
const callbacks = [];
const sandbox = {
  console: {log() {}, warn() {}, error() {}},
  YT: {PlayerState: {CUED: 5, PLAYING: 1, PAUSED: 2}},
  setTimeout(fn, ms) { callbacks.push({fn, ms}); return callbacks.length; },
  window: {addEventListener() {}},
  document: {getElementById() { return null; }},
};
vm.createContext(sandbox);
vm.runInContext(code + '\nwindow.__TestVJPlayer = VJPlayer;', sandbox, {filename: 'player.js'});
function createPlayer() {
  const api = {stop: 0, cue: [], state: -1};
  const playerApi = {
    stopVideo() { api.stop++; },
    cueVideoById(arg) { api.cue.push(arg.videoId); },
    getPlayerState() { return api.state; },
    getVideoData() { return {video_id: 'rank1'}; },
  };
  const p = Object.create(sandbox.window.__TestVJPlayer.prototype);
  const reports = [];
  p.players = {B: playerApi};
  p.nextPlayer = 'B';
  p.nextVideoId = 'rank1';
  p.playerVideoIds = {B: 'rank1'};
  p.playerTrackInfo = {B: null};
  p.playerMediaInfo = {B: null};
  p.failedVideoIds = {B: null};
  p.isReady = {B: false};
  p._lastPlayerState = {B: -1};
  p._preloadRequestSerial = 0;
  // The same-video fast path reuses an actual earlier physical cue.
  p._preloadRequestByPlayer = {B: {serial: 0, videoId: 'rank1', requestId: 'existing-cue'}};
  p._verifiedCueSerial = {B: null};
  p.clearPlaybackError = () => {};
  p.sendFeedback = (state, id) => reports.push([state, id]);
  p.sendPlayerSnapshot = () => reports.push(['snapshot', p.nextVideoId]);
  return {p, api, reports};
}
check('same-ID PRELOAD without retry does not need another cue', () => {
  const {p, api, reports} = createPlayer();
  p.handlePreload('rank1', null, null, false);
  assert.equal(api.cue.length, 0);
  assert.deepEqual(reports, [['snapshot', 'rank1']]);
});
check('forced PRELOAD of same ID stops stalled player and re-cues', () => {
  const {p, api, reports} = createPlayer();
  p.handlePreload('rank1', {title:'song'}, {videoTitle:'result'}, true);
  assert.equal(api.stop, 1);
  assert.deepEqual(api.cue, ['rank1']);
  assert.equal(p._lastPlayerState.B, null);
  assert.deepEqual(reports, [['preloading', 'rank1']]);
});
check('one-second fallback does not report ready when YouTube is still stalled', () => {
  callbacks.length = 0;
  const {p, reports} = createPlayer();
  p.handlePreload('rank1', null, null, true);
  assert.equal(callbacks.length, 1);
  callbacks[0].fn();
  assert(!reports.some(([s]) => s === 'ready'));
});
check('late timer from prior retry cannot fake readiness', () => {
  callbacks.length = 0;
  const {p, api, reports} = createPlayer();
  p.handlePreload('rank1', null, null, true);
  p.handlePreload('rank1', null, null, true);
  api.state = sandbox.YT.PlayerState.CUED;
  callbacks[0].fn();
  assert(!reports.some(([s]) => s === 'ready'));
  callbacks[1].fn();
  assert.equal(reports.filter(([s]) => s === 'ready').length, 1);
});
check('server forceReload field is forwarded to browser handler', () => {
  const {p} = createPlayer();
  const received = [];
  p.handlePreload = (...args) => received.push(args);
  p.processCommand({cmd: 'PRELOAD', videoId: 'rank1', forceReload: true});
  assert.equal(received[0][3], true);
  p.processCommand({cmd: 'PRELOAD', videoId: 'rank2'});
  assert.equal(received[1][3], false);
});
console.log(`${passed} player recovery fixture checks passed.`);
