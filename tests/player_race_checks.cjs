/* Offline regression tests for stale PRELOAD/PLAY events and finite waits. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '..', 'web', 'player.js'), 'utf8');
const timers = new Map(); let nextTimer=1; let errors = [];
const sandbox = {
  console: {log(){}, warn(){}, error(...args){ errors.push(args.join(' ')); }},
  YT: {PlayerState:{UNSTARTED:-1, ENDED:0, PLAYING:1, PAUSED:2, BUFFERING:3, CUED:5}},
  setTimeout(fn, ms){const id=nextTimer++;timers.set(id,{fn,ms});return id;},
  clearTimeout(id){timers.delete(id);},
  document: {getElementById(){return null;}},
  window: {addEventListener(){}},
};
vm.createContext(sandbox);
vm.runInContext(code+'\nwindow.VJPlayerUnderTest=VJPlayer;',sandbox,{filename:'player.js'});
function fixture(){
 timers.clear();errors=[];
 const api = {real:'old-video',state:-1,cues:[],plays:0,stops:0};
 const iframe = {
   cueVideoById(arg){api.cues.push(arg.videoId);},
   stopVideo(){api.stops++;}, playVideo(){api.plays++;},
   getPlayerState(){return api.state;},
   getVideoData(){return {video_id: api.real};},
 };
 const p=Object.create(sandbox.window.VJPlayerUnderTest.prototype);
 const feedback=[];
 Object.assign(p,{
   players:{B:iframe},currentPlayer:'A',nextPlayer:'B',currentVideoId:'old-video',
   nextVideoId:null,playerVideoIds:{A:'old-video',B:'old-video'},
   playerTrackInfo:{A:null,B:null},playerMediaInfo:{A:null,B:null},
   isReady:{A:true,B:false},failedVideoIds:{A:null,B:null},
   _preloadRequestSerial:0,_preloadRequestByPlayer:{A:null,B:null},
   _verifiedCueSerial:{A:null,B:null},_playRequestSerial:0,_playPending:null,
   _playRequestByPlayer:{A:null,B:null},
   _lastPlayerState:{A:null,B:null},_lastPlayingAtMs:{A:0,B:0},
   clearPlaybackError(){},showErrorNotification(){},forceCaptionsOff(){},
   sendFeedback:(state,videoId,playerId,options)=>feedback.push({state,videoId,playerId,options}),
   updateTrackInfoOverlay(){},
 });
 return {p,api,feedback};
}
let passed=0;
function test(name,fun){fun();passed++;console.log('PASS:',name);}
function timersWithMs(ms){return [...timers.entries()].filter(([_,t])=>t.ms===ms);}
function runTimerByMs(ms){const found=timersWithMs(ms); assert(found.length,`no ${ms}ms timer`);const [id,t]=found[0];timers.delete(id);t.fn();}

test('PRELOAD snapshot never claims READY from requested ID alone',()=>{
 const {p,feedback}=fixture();p.handlePreload('new-video',null,null,false,'cue-1');
 p.sendPlayerSnapshot('B');
 assert.deepEqual(feedback.map(x=>x.state),['preloading','preloading']);
});
test('old CUED for previous video is ignored after new PRELOAD',()=>{
 const {p,feedback}=fixture();p.handlePreload('new-video',null,null,false,'cue-1');
 p.onPlayerStateChange('B',{data:sandbox.YT.PlayerState.CUED});
 assert.equal(p.isReady.B,false);assert.equal(p._lastPlayerState.B,null);
 assert(!feedback.some(x=>x.state==='ready'));
});
test('CUED after actual video matches reports READY with current request',()=>{
 const {p,api,feedback}=fixture();p.handlePreload('new-video',null,null,false,'cue-1');
 api.real='new-video';api.state=sandbox.YT.PlayerState.CUED;
 p.onPlayerStateChange('B',{data:sandbox.YT.PlayerState.CUED});
 assert.equal(p.isReady.B,true);assert.equal(p.preloadIsVerified('B','new-video'),true);
 assert(feedback.some(x=>x.state==='ready'&&x.videoId==='new-video'));
});
test('retry invalidates stale fallback, even with the same video ID',()=>{
 const {p,api,feedback}=fixture();p.handlePreload('new-video',null,null,false,'cue-1');
 const oldTimer=timersWithMs(1000)[0][1].fn;
 p.handlePreload('new-video',null,null,true,'cue-2');
 api.real='new-video';api.state=sandbox.YT.PlayerState.CUED;
 oldTimer();assert.equal(feedback.filter(x=>x.state==='ready').length,0);
 runTimerByMs(1000); // old timer: inert; next current timer remains
 runTimerByMs(1000);
 assert.equal(feedback.filter(x=>x.state==='ready').length,1);
 assert.equal(p._preloadRequestByPlayer.B.requestId,'cue-2');
});
test('PLAY never polls forever when target does not become ready',()=>{
 const {p,feedback}=fixture();p.nextVideoId='new-video';p._playRequestSerial=1;
 p.waitForReadyAndSwitch('new-video','B');
 for(let i=0;i<130;i++){
   if(!timersWithMs(100).length)break;
   runTimerByMs(100);
 }
 assert.equal(timersWithMs(100).length,0);
 assert(feedback.some(x=>x.state==='error'&&x.options.errorCode==='play-ready-timeout'));
 assert.equal(p.nextVideoId,null);
});
test('switch does not fabricate PLAYING; actual start timeout reports error',()=>{
 const {p,feedback}=fixture();p.nextVideoId='new-video';
 p.switchAndPlay('new-video','B');
 assert(!feedback.some(x=>x.state==='playing'));
 runTimerByMs(12000);
 assert(feedback.some(x=>x.state==='error'&&x.options.errorCode==='play-start-timeout'));
});
test('actual PLAYING event satisfies and disarms PLAY startup watchdog',()=>{
 const {p,api,feedback}=fixture();p.nextVideoId='new-video';
 p._playRequestByPlayer.B={requestId:'play-1',videoId:'new-video'};
 p.switchAndPlay('new-video','B');
 api.real='new-video';api.state=sandbox.YT.PlayerState.PLAYING;
 p.onPlayerStateChange('B',{data:sandbox.YT.PlayerState.PLAYING});
 assert(feedback.some(x=>x.state==='playing'&&x.playerId==='B'));
 assert.equal(p._playPending,null);assert.equal(timersWithMs(12000).length,0);
});
test('commandId is preserved on PRELOAD and PLAY handlers',()=>{
 const {p}=fixture();const observed=[];
 p.handlePreload=(...args)=>observed.push(['cue',args]);
 p.handlePlay=(...args)=>observed.push(['play',args]);
 p.processCommand({cmd:'PRELOAD', videoId:'x', commandId:'cue-xyz'});
 p.processCommand({cmd:'PLAY', videoId:'x', commandId:'play-xyz'});
 assert.equal(observed[0][1][4],'cue-xyz');
 assert.equal(observed[1][1][3],'play-xyz');
});
console.log(`${passed} race checks passed.`);
