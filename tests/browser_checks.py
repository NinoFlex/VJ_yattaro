"""Offline Chromium fixture checks. Does not contact Shazam or test Windows WebView2.

The fixture runs only on localhost. Test copies of the scripts use a localhost
guard; production scripts remain restricted to the HTTPS Shazam origin.

Optional: pip install playwright && playwright install chromium
Run: python tests/browser_checks.py [--chromium /path/to/chromium]
"""
import argparse
import base64
import io
import json
import math
from pathlib import Path
import struct
import wave
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'native/ShazamWebViewBridge/Scripts'
ID = 'a' * 32
NEXT = 'b' * 32
HTML = '''<!doctype html><html><body>
<h1>Music discovery, charts and lyrics</h1>
<a href="https://music.apple.com/jp/album/chart/1111111?i=2222222">Chart song</a>
<a aria-label="Shazam homepage" href="/">Shazam</a>
<button id="start" aria-label="Shazam music recognition button" style="position:fixed;right:20px;bottom:20px;width:80px;height:80px">Shazam</button>
</body></html>'''
TRACK = {'matches': [{'id': 'matched'}], 'track': {
    'title': '\u7d05\u84ee\u83ef', 'subtitle': 'LiSA', 'url': 'https://www.shazam.com/track/1234567/song',
    'hub': {'options': [{'actions': [{'uri': 'https://music.apple.com/jp/album/test/1234567?i=1825279997'}]}]}}}


def make_wav():
    file = io.BytesIO()
    with wave.open(file, 'wb') as wav:
        wav.setparams((1, 2, 16000, 96000, 'NONE', 'not compressed'))
        wav.writeframes(b''.join(struct.pack('<h', int(10000 * math.sin(2 * math.pi * 440 * i / 16000))) for i in range(96000)))
    return base64.b64encode(file.getvalue()).decode('ascii')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chromium', default=None)
    args = parser.parse_args()
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium, headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context(viewport={'width': 1000, 'height': 720})
        def fulfill(route):
            if route.request.url.endswith('/recognition-fixture'):
                route.fulfill(status=200, content_type='application/json', body=json.dumps(TRACK))
            else:
                route.fulfill(status=200, content_type='text/html', body=HTML)
        context.route('**/*', fulfill)
        context.add_init_script('''window.__messages=[]; window.chrome=window.chrome||{};
            window.chrome.webview={postMessage(m){window.__messages.push(m)}};
            window.__speakerConnections=0;
            const _connect=AudioNode.prototype.connect;
            AudioNode.prototype.connect=function(dest,...args){
              if(dest instanceof AudioDestinationNode) window.__speakerConnections++;
              return _connect.call(this,dest,...args);
            };''')
        for name in ('audio_bridge.js', 'result_observer.js'):
            production = (SCRIPTS / name).read_text(encoding='utf-8')
            assert "['www.shazam.com', 'shazam.com']" in production
            # Test-only fixture adaptation. No production files are modified.
            fixture = production.replace("['www.shazam.com', 'shazam.com']", "['localhost']")
            fixture = fixture.replace("location.protocol !== 'https:'", "location.protocol !== 'http:'")
            fixture = fixture.replace("u.protocol !== 'https:'", "u.protocol !== 'http:'")
            context.add_init_script(script=fixture)
        page = context.new_page()
        page.goto('http://localhost:8765/ja-jp')
        page.evaluate('(id)=>__vjResults.arm(id)', ID)
        page.evaluate('__vjResults.scan()')
        assert not page.evaluate('__messages.filter(m=>m.type==="candidate")')
        checks.append('homepage charts are not recognition results')

        picked = page.evaluate((SCRIPTS / 'find_button.js').read_text())
        assert picked and picked['x'] > 850 and 'recognition' in picked['label']
        checks.append('recognition button selected, not homepage logo')

        page.evaluate('''() => {const d=document.createElement('div');d.id='overlay';
          d.style='position:fixed;inset:0;z-index:100000;background:white';document.body.append(d)}''')
        assert page.evaluate((SCRIPTS / 'find_button.js').read_text()) is None
        page.evaluate('document.getElementById("overlay").remove()')
        checks.append('covered buttons are not clicked through consent overlays')

        page.evaluate('(p)=>__vjResults.inspectRecognition(p, "' + ID + '")', TRACK)
        msg = page.evaluate('__messages.filter(m=>m.type==="candidate").at(-1)')
        assert msg['title'] == '\u7d05\u84ee\u83ef' and msg['artist'] == 'LiSA' and msg['appleTrackId'] == '1825279997'
        checks.append('recognition JSON extracts title, artist, exact Apple ID')

        page.evaluate('(id)=>{__messages=[];__vjResults.arm(id)}', NEXT)
        page.evaluate('(p)=>__vjResults.inspectRecognition(p, "' + ID + '")', TRACK)
        assert not page.evaluate('__messages.length')
        checks.append('late previous-cycle response ignored')

        page.evaluate('history.pushState({}, "", "/ja-jp/track/1234567/test")')
        assert not page.evaluate('__messages.filter(m=>m.type==="candidate")')
        checks.append('legacy Shazam track ID is not an Apple ID')
        page.evaluate('history.pushState({}, "", "/ja-jp/song/1825279997/test")')
        msg = page.evaluate('__messages.filter(m=>m.type==="candidate").at(-1)')
        assert msg['appleTrackId'] == '1825279997'
        page.evaluate('''()=>{document.body.innerHTML='<h1>Page not found</h1>';__vjResults.scan()}''')
        assert all(not m.get('title') for m in page.evaluate('__messages.filter(m=>m.type==="candidate")'))
        checks.append('song route captured without publishing 404 title')

        page.goto('http://localhost:8765/ja-jp')
        page.evaluate('(id)=>__vjResults.arm(id)', ID)
        page.evaluate('fetch("/recognition-fixture").then(r=>r.json())')
        page.wait_for_function('__messages.some(m=>m.evidence==="recognition-response")')
        checks.append('observes actual fetch response clone without separate recognition request')

        page.evaluate('(id)=>{__messages=[];__vjResults.arm(id)}', NEXT)
        page.evaluate('''()=>new Promise(resolve=>{const x=new XMLHttpRequest();x.open('GET','/recognition-fixture');
          x.onload=()=>resolve(true);x.send()})''')
        page.wait_for_function('__messages.some(m=>m.evidence==="recognition-response")')
        checks.append('observes XHR response')

        page.evaluate('(id)=>{__messages=[];__vjResults.arm(id)}', ID)
        page.evaluate('''()=>{const dialog=document.createElement('div');dialog.setAttribute('role','dialog');
          dialog.innerHTML='<h2>New result</h2><a href="/artist/1234567/a">Performer</a>';
          document.body.append(dialog)}''')
        page.wait_for_function('__messages.some(m=>m.title==="New result")')
        checks.append('scoped newly displayed result dialog is captured')

        page.goto('http://localhost:8765/ja-jp')
        result = page.evaluate('(args)=>__vjAudioBridge.load(args[0],args[1])', [make_wav(), ID])
        assert result['ok'] and 5.99 < result['duration'] < 6.01
        page.evaluate('''()=>{document.querySelector('#start').onclick=async()=>{
          try {window.__inputStream=await navigator.mediaDevices.getUserMedia({audio:true});}
          catch(e){window.__inputError=e.toString()}
        }}''')
        page.click('#start')
        page.wait_for_function('window.__inputStream || window.__inputError')
        assert not page.evaluate('window.__inputError || ""')
        stats = page.evaluate('''async()=>{
          const ctx=new AudioContext(); await ctx.resume();
          const source=ctx.createMediaStreamSource(__inputStream);
          const analyser=ctx.createAnalyser();analyser.fftSize=2048;
          const sink=ctx.createMediaStreamDestination();
          source.connect(analyser);analyser.connect(sink);
          const recorder=new MediaRecorder(sink.stream);recorder.start();
          await new Promise(r=>setTimeout(r,800));
          const samples=new Float32Array(analyser.fftSize);analyser.getFloatTimeDomainData(samples);
          const rms=Math.sqrt(samples.reduce((a,x)=>a+x*x,0)/samples.length);
          recorder.stop(); await ctx.close();
          return {rms,tracks:__inputStream.getAudioTracks().length,speakers:__speakerConnections};
        }''')
        assert stats['rms'] > 0.1 and stats['tracks'] == 1, stats
        assert stats['speakers'] == 0, stats
        checks.append('WAV becomes a non-silent microphone MediaStream without any speaker connection')
        page.evaluate('__vjAudioBridge.stop()')
        error = page.evaluate('navigator.mediaDevices.getUserMedia({audio:true}).then(()=>"unexpected",e=>e.name)')
        assert error == 'NotReadableError'
        assert page.evaluate('__inputStream.getAudioTracks()[0].readyState') == 'ended'
        checks.append('stop ends tracks and never falls back to another OS microphone')

        page.goto('http://127.0.0.1:8765/')
        assert page.evaluate('typeof __vjAudioBridge') == 'undefined'
        assert page.evaluate('typeof __vjResults') == 'undefined'
        checks.append('injection is restricted to Shazam top-level origin')
        browser.close()
    for check in checks:
        print('PASS:', check)
    print(f'{len(checks)} offline Chromium fixture checks passed.')


if __name__ == '__main__':
    main()
