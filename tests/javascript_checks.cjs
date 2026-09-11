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

function routeEvidenceFixture({headingTitle = '', jsonLd = null} = {}) {
  class Element {
    constructor(text = '') { this.innerText = text; this.textContent = text; this.parentElement = null; }
    getBoundingClientRect() { return {width: 320, height: 40}; }
    closest(selector) { return selector.includes('article') ? this : null; }
    querySelectorAll() { return []; }
  }
  const heading = headingTitle ? new Element(headingTitle) : null;
  const script = jsonLd ? {textContent: JSON.stringify(jsonLd)} : null;
  const document = {
    querySelectorAll(selector) {
      if (selector === 'h1,[data-testid*="title" i]') return heading ? [heading] : [];
      if (selector === 'script[type="application/ld+json"]') return script ? [script] : [];
      return [];
    }
  };
  const world = {
    URL, Set, Object, String, Array, Element, document,
    location: {href: 'https://www.shazam.com/ja-jp/song/6793804469/star'},
    getComputedStyle: () => ({display: 'block', visibility: 'visible', opacity: '1'})
  };
  world.window = world;
  const source = fs.readFileSync(path.join(scripts, 'route_evidence.js'), 'utf8')
    .replace('__BASELINE_TRACK_IDS__', '[]');
  return vm.runInContext(source, vm.createContext(world), {filename: 'route_evidence.js'});
}


function routeSlugDomFixture({semanticHeading = false} = {}) {
  class Element {
    constructor(tag, text, rect) {
      this.tag = tag;
      this.innerText = text;
      this.textContent = text;
      this.parentElement = null;
      this.children = [];
      this.rect = rect;
    }
    getBoundingClientRect() { return this.rect; }
    closest() { return null; }
    contains(other) {
      for (let e = other; e; e = e.parentElement) if (e === this) return true;
      return false;
    }
    querySelectorAll(selector) {
      const tags = new Set(selector.split(',').map(x => x.trim().replace(/\[.*$/, '').toLowerCase()).filter(Boolean));
      const out = [];
      const walk = node => {
        for (const child of node.children) {
          if (tags.has(child.tag)) out.push(child);
          walk(child);
        }
      };
      walk(this);
      return out;
    }
  }
  const title = new Element(semanticHeading ? 'h1' : 'div', 'たくさん!',
    {left:100, top:100, right:310, bottom:142, width:210, height:42});
  const artist = new Element('span', 'Anastasia (CV: Sumire Uesaka)',
    {left:100, top:148, right:390, bottom:172, width:290, height:24});
  const stats = new Element('span', '308 · Anime',
    {left:100, top:180, right:210, bottom:200, width:110, height:20});
  const container = new Element('div', 'たくさん! Anastasia (CV: Sumire Uesaka) 308 · Anime',
    {left:80, top:80, right:430, bottom:220, width:350, height:140});
  container.children = [title, artist, stats];
  for (const child of container.children) child.parentElement = container;
  const all = [container, title, artist, stats];
  const document = {
    body: container,
    querySelector() { return container; },
    querySelectorAll(selector) {
      if (selector === 'h1,[data-testid*="title" i]') return semanticHeading ? [title] : [];
      if (selector === 'script[type="application/ld+json"]') return [];
      if (selector === 'h1,h2,h3,div,span,p') return all;
      return [];
    }
  };
  const world = {
    URL, Set, Object, String, Array, Element, document,
    location: {href: 'https://www.shazam.com/track/436429664/%E3%81%9F%E3%81%8F%E3%81%95%E3%82%93'},
    getComputedStyle: () => ({display: 'block', visibility: 'visible', opacity: '1'})
  };
  world.window = world;
  const source = fs.readFileSync(path.join(scripts, 'route_evidence.js'), 'utf8')
    .replace('__BASELINE_TRACK_IDS__', '[]');
  return vm.runInContext(source, vm.createContext(world), {filename: 'route_evidence.js'});
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

  const delayedArtist = routeEvidenceFixture({
    headingTitle: 'Star',
    jsonLd: {'@type':'MusicRecording', name:'Star', byArtist:{name:'日本語アーティスト'}}
  });
  assert.equal(delayedArtist.evidence, 'jsonld');
  assert.equal(delayedArtist.artist, '日本語アーティスト');
  ok('route evidence does not return an artist-less heading before complete JSON-LD');

  const routeOnly = routeEvidenceFixture();
  assert.equal(routeOnly.evidence, 'route-only');
  assert.equal(routeOnly.artist, '');
  ok('route-only remains the bounded fallback when detailed metadata never appears');

  const nearbyHeading = routeSlugDomFixture({semanticHeading: true});
  assert.equal(nearbyHeading.title, 'たくさん!');
  assert.equal(nearbyHeading.artist, 'Anastasia (CV: Sumire Uesaka)');
  assert.equal(nearbyHeading.evidence, 'track-heading-nearby');
  ok('plain nearby artist text is recovered below a semantic Shazam title');

  const slugAnchored = routeSlugDomFixture();
  assert.equal(slugAnchored.title, 'たくさん!');
  assert.equal(slugAnchored.artist, 'Anastasia (CV: Sumire Uesaka)');
  assert.equal(slugAnchored.evidence, 'route-slug-dom');
  ok('route slug anchors visible title and nearby artist when Shazam uses plain div/span markup');

  const bridgeSource = fs.readFileSync(path.join(__dirname, '..', 'native', 'ShazamWebViewBridge', 'BridgeHost.cs'), 'utf8');
  assert.match(bridgeSource, /RouteEvidenceWindow\s*=\s*TimeSpan\.FromSeconds\(4\)/);
  ok('route metadata polling window is extended beyond the former one-second race');
  console.log(`${total} JavaScript mock fixture checks passed.`);
})().catch(e=>{console.error(e);process.exit(1)});
