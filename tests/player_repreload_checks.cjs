/* Production player.js and its real feedback serializer, with fake YouTube/HTTP.
 * No website or external API is called. Cue serials are physical loads; command
 * IDs are feedback owners and must follow a same-video PRELOAD replacement.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync(path.join(__dirname, '..', 'web', 'player.js'), 'utf8');
let passed = 0, failed = 0;
function test(label, fn) {
  try { fn(); passed++; console.log('PASS:', label); }
  catch (e) { failed++; console.error('FAIL:', label, '\n' + e.stack); }
}
function fixture() {
  const timers = [], messages = [], containers = {};
  const sandbox = {
    console: {log(){}, warn(){}, error(){}},
    YT: {PlayerState: {UNSTARTED:-1, ENDED:0, PLAYING:1, PAUSED:2, BUFFERING:3, CUED:5}},
    setTimeout(fn, ms) { const t={fn, ms, live:true}; timers.push(t); return t; },
    clearTimeout(t) { if (t) t.live=false; },
    document: {getElementById(id){ return containers[id] || null; }},
    window: {addEventListener(){}},
    fetch(url, options) { messages.push(JSON.parse(options.body)); return Promise.resolve({ok:true}); },
  };
  vm.createContext(sandbox);
  vm.runInContext(code + '\nwindow.TestPlayer=VJPlayer;', sandbox, {filename:'player.js'});
  const api = {actual:'old-video', state:-1, cues:[], stops:0, plays:0};
  const iframe = {
    cueVideoById(arg) {api.cues.push(arg.videoId);},
    stopVideo() {api.stops++;}, playVideo() {api.plays++;},
    getVideoData(){return {video_id:api.actual};}, getPlayerState(){return api.state;},
  };
  const p = Object.create(sandbox.window.TestPlayer.prototype);
  Object.assign(p, {
    players:{B:iframe}, currentPlayer:'A', nextPlayer:'B', currentVideoId:'output-video', nextVideoId:null,
    playerVideoIds:{A:'output-video',B:'old-video'}, playerTrackInfo:{A:null,B:null}, playerMediaInfo:{A:null,B:null},
    isReady:{A:true,B:false}, failedVideoIds:{A:null,B:null},
    _preloadRequestSerial:0, _preloadRequestByPlayer:{A:null,B:null}, _verifiedCueSerial:{A:null,B:null},
    _playRequestSerial:0, _playRequestByPlayer:{A:null,B:null}, _playPending:null,
    _lastPlayerState:{A:1,B:null}, _lastPlayingAtMs:{A:0,B:0},
    feedbackUrl:'http://fixture.invalid/feedback', sessionId:'fixture-session',
    forceCaptionsOff(){}, updateTrackInfoOverlay(){}, clearPlaybackError(id){this.failedVideoIds[id]=null;},
  });
  function cue(id='old-cue') {p.handlePreload('same-video',null,null,false,id);}
  function ready(state=5) {
    api.actual='same-video'; api.state=state;
    p.onPlayerStateChange('B',{data:state});
  }
  return {p, api, messages, timers, containers, cue, ready};
}

test('ready same-video replacement reports NEW command ID without another cue', () => {
  const f=fixture();f.cue();f.ready();f.cue('new-cue');
  assert.equal(f.messages.at(-1).state,'ready');
  assert.equal(f.messages.at(-1).requestId,'new-cue');
  assert.equal(f.api.cues.length,1);assert.equal(f.api.stops,0);assert.equal(f.api.plays,0);
});
test('same-video replacement preserves physical cue generation and verified state', () => {
  const f=fixture();f.cue();f.ready();const serial=f.p._preloadRequestByPlayer.B.serial;
  f.cue('new-cue');assert.equal(f.p._preloadRequestByPlayer.B.serial,serial);
  assert.equal(f.p._verifiedCueSerial.B,serial);assert(f.p.preloadIsVerified('B','same-video'));
});
test('loading same-video replacement reports preloading with NEW command ID', () => {
  const f=fixture();f.cue();f.cue('new-cue');
  assert.equal(f.messages.at(-1).requestId,'new-cue');assert.equal(f.messages.at(-1).state,'preloading');
  assert(!f.messages.some(x=>x.state==='ready'));assert.equal(f.api.cues.length,1);
});
test('pending one-shot readiness fallback is not invalidated by token replacement', () => {
  const f=fixture();f.cue();const fallback=f.timers.find(t=>t.ms===1000).fn;f.cue('new-cue');
  f.api.actual='same-video';f.api.state=5;fallback();
  assert.equal(f.messages.at(-1).state,'ready');assert.equal(f.messages.at(-1).requestId,'new-cue');
});
test('CUED callback after token replacement resolves the NEW request', () => {
  const f=fixture();f.cue();f.cue('new-cue');f.ready();
  assert.equal(f.messages.at(-1).state,'ready');assert.equal(f.messages.at(-1).requestId,'new-cue');
});
test('stale CUED for another video remains rejected after token replacement', () => {
  const f=fixture();f.cue();f.cue('new-cue');f.p.onPlayerStateChange('B',{data:5});
  assert(!f.messages.some(x=>x.state==='ready'));assert.equal(f.p.isReady.B,false);
});
test('request bookkeeping alone must not fabricate readiness', () => {
  const f=fixture();f.cue();f.api.actual='same-video';f.api.state=5;
  f.p.isReady.B=true;f.p._lastPlayerState.B=5;f.cue('new-cue');
  assert.equal(f.messages.at(-1).state,'preloading');assert(!f.p.preloadIsVerified('B','same-video'));
});
test('confirmed old ready state cannot mask a different actual iframe video', () => {
  const f=fixture();f.cue();f.ready();f.api.actual='wrong-video';f.cue('new-cue');
  assert.equal(f.messages.at(-1).state,'preloading');assert.equal(f.messages.at(-1).requestId,'new-cue');
});
test('paused prepared video acknowledges NEW PRELOAD as ready rather than paused', () => {
  const f=fixture();f.cue();f.ready(2);f.cue('new-cue');
  assert.equal(f.messages.at(-1).state,'ready');assert.equal(f.messages.at(-1).requestId,'new-cue');
});
test('standby PLAYING snapshot cannot leak an earlier PLAY command ID', () => {
  const f=fixture();f.cue();f.ready(1);
  f.p._playRequestByPlayer.B={videoId:'same-video',requestId:'old-play'};
  f.cue('new-cue');assert.equal(f.messages.at(-1).state,'ready');
  assert.equal(f.messages.at(-1).requestId,'new-cue');assert.equal(f.p._playRequestByPlayer.B,null);
});
test('replacement refreshes metadata without changing current output', () => {
  const f=fixture();f.cue();f.ready();const track={title:'updated title'},media={durationText:'3:00'};
  f.p.handlePreload('same-video',track,media,false,'new-cue');
  assert.deepEqual(f.messages.at(-1).trackInfo,track);assert.equal(f.messages.at(-1).mediaInfo.durationText,'3:00');
  assert.equal(f.p.currentPlayer,'A');assert.equal(f.p.currentVideoId,'output-video');
});
test('forced same-video reload still re-cues and invalidates old fallback', () => {
  const f=fixture();f.cue();const oldFallback=f.timers.find(t=>t.ms===1000).fn;
  f.cue('new-cue');f.p.handlePreload('same-video',null,null,true,'retry-cue');
  assert.equal(f.api.cues.length,2);assert.equal(f.api.stops,1);
  f.api.actual='same-video';f.api.state=5;const count=f.messages.length;oldFallback();
  assert.equal(f.messages.length,count);f.timers.filter(t=>t.ms===1000).at(-1).fn();
  assert.equal(f.messages.at(-1).requestId,'retry-cue');assert.equal(f.messages.at(-1).state,'ready');
});
test('replacement by a different video invalidates old cue callback', () => {
  const f=fixture();f.cue();const oldFallback=f.timers.find(t=>t.ms===1000).fn;f.cue('new-cue');
  f.p.handlePreload('different-video',null,null,false,'other-cue');f.api.actual='same-video';f.api.state=5;
  const count=f.messages.length;oldFallback();assert.equal(f.messages.length,count);
  assert.equal(f.p._preloadRequestByPlayer.B.requestId,'other-cue');assert.equal(f.api.cues.length,2);
});
test('fallback iframe pending load publishes newest token without restarting iframe', () => {
  const f=fixture(), loads=[];delete f.p.players.B;
  f.containers.playerBContainer={dataset:{embedVideoId:'same-video',embedFallback:'1'},
    querySelector(){return {addEventListener(event,fn){loads.push(fn);}};}};
  let embeds=0;f.p.ensureIframeFor=()=>{embeds++;return true;};f.cue();f.cue('new-cue');
  assert.equal(embeds,1);assert.equal(loads.length,1);loads[0]();
  assert.equal(f.messages.at(-1).state,'ready');assert.equal(f.messages.at(-1).requestId,'new-cue');
});
test('already loaded fallback iframe reuses readiness for new token', () => {
  const f=fixture(), loads=[];delete f.p.players.B;
  f.containers.playerBContainer={dataset:{embedVideoId:'same-video',embedFallback:'1'},
    querySelector(){return {addEventListener(event,fn){loads.push(fn);}};}};
  f.p.ensureIframeFor=()=>true;f.cue();loads[0]();f.cue('new-cue');
  assert.equal(f.messages.at(-1).state,'ready');assert.equal(f.messages.at(-1).requestId,'new-cue');
});
test('same-video identity without cue provenance starts a real new cue', () => {
  const f=fixture();f.p.nextVideoId='same-video';f.p.playerVideoIds.B='same-video';
  f.cue('new-cue');assert.equal(f.api.cues.length,1);assert.equal(f.messages.at(-1).requestId,'new-cue');
  assert.equal(f.messages.at(-1).state,'preloading');
});
test('failed same-video state is not re-acknowledged as ready', () => {
  const f=fixture();f.cue();f.ready();f.p.failedVideoIds.B='same-video';f.p.isReady.B=false;
  f.cue('new-cue');assert.equal(f.api.cues.length,2);assert.equal(f.messages.at(-1).state,'preloading');
  assert.equal(f.messages.at(-1).requestId,'new-cue');
});
test('legacy idless repeat preserves existing cue token and does not reload', () => {
  const f=fixture();f.cue();f.ready();f.p.handlePreload('same-video',null,null);
  assert.equal(f.messages.at(-1).requestId,'old-cue');assert.equal(f.api.cues.length,1);
});
console.log(`${passed} passed, ${failed} failed (same-video PRELOAD checks).`);
if(failed) process.exitCode=1;
