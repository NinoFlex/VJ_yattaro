/* Offline unit fixtures. These emulate Web APIs; they do not test real WebView2 audio. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const scripts = path.join(__dirname, '..', 'native', 'ShazamWebViewBridge', 'Scripts');
const a = 'a'.repeat(32), b = 'b'.repeat(32);
let total = 0;
function ok(name) { total++; console.log('PASS:', name); }
function fixture(host = 'www.shazam.com') {
  const messages = [];
  const location = {href: `https://${host}/ja-jp`, hostname: host, protocol: 'https:'};
  const body = {querySelector: () => null, querySelectorAll: () => []};
  const document = {body, querySelectorAll: () => [], querySelector: () => null};
  let nativeCalls = 0;
  const connections = [];
  class AudioContext {
    constructor() {this.state = 'suspended';this.currentTime=0;this.destination={speaker:true};}
    async decodeAudioData() {return {duration:6,numberOfChannels:1,sampleRate:48000};}
    async resume(){this.state='running';}
    async close(){this.state='closed';}
    createMediaStreamDestination(){
      const track={readyState:'live',addEventListener(){},stop(){this.readyState='ended'}};
      return {channelCount:2, stream:{getAudioTracks:()=>[track],getTracks:()=>[track]}};
    }
    createBufferSource(){return {connect(dest){connections.push(dest)},disconnect(){},start(){},stop(){}};}
  }
  class XMLHttpRequest {send(){} addEventListener(){}}
  const world = {console, URL, Uint8Array, Set, Object, String, Array, AudioContext, XMLHttpRequest,
    DOMException, navigator:{mediaDevices:{async getUserMedia(){nativeCalls++;return {};}}},
    location, document, history:{pushState(s,t,url){location.href=new URL(url,location.href).href;},replaceState(s,t,url){location.href=new URL(url,location.href).href;}},
    MutationObserver:class {observe(){}},
    addEventListener(){}, queueMicrotask, getComputedStyle:()=>({display:'block',visibility:'visible'}),
    atob:s=>Buffer.from(s,'base64').toString('binary'),
    fetch:async()=>({headers:{get:()=> 'application/json'},clone(){return {text:async()=>JSON.stringify(world.payload)}}}),
    chrome:{webview:{postMessage:value=>messages.push(value)}}};
  world.window=world;world.top=world;
  const ctx=vm.createContext(world);
  for(const file of ['audio_bridge.js','result_observer.js'])
    vm.runInContext(fs.readFileSync(path.join(scripts,file),'utf8'),ctx,{filename:file});
  return {ctx,world,messages,connections,nativeCalls:()=>nativeCalls};
}
(async()=>{
  const f=fixture();
  f.world.__vjResults.arm(a);
  f.world.__vjResults.scan();
  assert.equal(f.messages.length,0);ok('home scan does not publish chart IDs');
  const track={matches:[{id:'matched'}],track:{title:'Gurenge',subtitle:'LiSA',url:'https://www.shazam.com/track/1234567/x',
    hub:{options:[{actions:[{uri:'https://music.apple.com/jp/album/x/1234567?i=1825279997'}]}]}}};
  f.world.__vjResults.inspectRecognition(track,a);
  assert.equal(f.messages.at(-1).title,'Gurenge');
  assert.equal(f.messages.at(-1).artist,'LiSA');
  assert.equal(f.messages.at(-1).appleTrackId,'1825279997');
  assert.equal(f.messages.at(-1).shazamTrackId,'1234567');ok('recognition metadata keeps Apple and Shazam IDs separate');
  f.world.__vjResults.arm(b);f.messages.length=0;
  f.world.__vjResults.inspectRecognition(track,a);
  assert.equal(f.messages.length,0);ok('stale request result rejected');
  f.world.__vjResults.inspectRecognition({track:track.track},b);
  assert.equal(f.messages.length,0);ok('catalog-like JSON without matches is rejected');
  f.world.__vjResults.inspectRecognition({matches:[],timestamp:1234567890,timezone:'Asia/Tokyo'},b);
  assert.equal(f.messages.at(-1).type,'no-match');ok('definitive empty recognition response triggers immediate retry signal');
  f.messages.length=0;
  const failNode={innerText:'曲が見つかりませんでした',textContent:'曲が見つかりませんでした',
    getBoundingClientRect:()=>({width:100,height:20})};
  f.world.document.querySelectorAll=sel=>sel.includes('[role=\"alert\"]')?[failNode]:[];
  f.world.__vjResults.arm(a);f.world.__vjResults.scan();
  assert.equal(f.messages.at(-1).type,'no-match');ok('visible Shazam failure text triggers immediate retry signal');
  f.world.document.querySelectorAll=()=>[];
  f.messages.length=0;
  f.world.history.pushState({},'', '/ja-jp/track/1234567/name');
  assert.equal(f.messages.at(-1).shazamTrackId,'1234567');
  assert.equal(f.messages.at(-1).appleTrackId,'');ok('/track route is captured only as a Shazam ID');
  f.messages.length=0;
  f.world.history.pushState({},'', '/ja-jp/song/1825279997/name');
  assert.equal(f.messages.at(-1).shazamTrackId,'1825279997');
  assert.equal(f.messages.at(-1).appleTrackId,'');ok('/song route is never reinterpreted as an Apple ID');
  f.world.__vjResults.arm(a);f.messages.length=0;
  f.world.__vjResults.inspectRecognition({matches:[{}],track:{title:'Page not found',subtitle:'A'}},a);
  assert.equal(f.messages.length,0);ok('404 cannot become a track title');
  f.world.__vjResults.inspectRecognition({matches:[{}],track:{title:'概要',subtitle:'Kia Mazzi',hub:{options:[{actions:[{uri:'https://music.apple.com/jp/album/trilogy/763630962?i=763630973'}]}]}}},a);
  assert.equal(f.messages.length,0);ok('Shazam UI label Overview cannot become a track title');
  f.world.__vjResults.inspectRecognition({matches:[{}],track:{title:'Shazam フッター',subtitle:'Metizone'}},a);
  assert.equal(f.messages.length,0);ok('Shazam footer UI label cannot become a track title');
  assert.equal(f.world.__vjResults.appleId('https://music.apple.com/jp/album/x/1234567'),'');
  assert.equal(f.world.__vjResults.appleId('https://evil.test/?i=1825279997'),'');ok('album and untrusted URLs are rejected');
  f.world.payload=track;
  await f.world.fetch('/recognition-fixture');await new Promise(setImmediate);
  assert.equal(f.messages.at(-1).evidence,'recognition-response');ok('fetch observer receives response clone');
  const loaded=await f.world.__vjAudioBridge.load(Buffer.from('fixture').toString('base64'),a);
  assert.equal(loaded.ok,true);
  const stream=await f.world.navigator.mediaDevices.getUserMedia({audio:true});
  assert.equal(stream.getAudioTracks()[0].readyState,'live');
  assert.equal(f.nativeCalls(),0);
  assert(f.connections.length>0 && f.connections.every(x=>!x.speaker));ok('audio graph supplies media stream without native mic or speaker routing (mock)');
  f.world.__vjAudioBridge.stop();
  assert.equal(stream.getAudioTracks()[0].readyState,'ended');
  await assert.rejects(()=>f.world.navigator.mediaDevices.getUserMedia({audio:true}),{name:'NotReadableError'});
  assert.equal(f.nativeCalls(),0);ok('stop closes supplied audio with no OS microphone fallback');
  const other=fixture('example.test');
  assert.equal(other.world.__vjAudioBridge,undefined);
  assert.equal(other.world.__vjResults,undefined);ok('production origin guards leave other sites untouched');
  console.log(`${total} JavaScript mock fixture checks passed.`);
})().catch(e=>{console.error(e);process.exit(1)});
