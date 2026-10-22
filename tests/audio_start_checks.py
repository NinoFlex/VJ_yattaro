"""Offline Chromium tests of production button/probe/capture-guard JavaScript.

No website, server, WebView2, C# or physical microphone is used. about:blank holds
real DOM and trusted mouse events. The scripts receive an explicit fixture-only
`location` parameter so no network navigation (including localhost) is needed.
The actual audio bridge supplies its own MediaStream; native mic calls are counted.
"""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'native/ShazamWebViewBridge/Scripts'
ID = 'a' * 32
HOME = 'https://www.shazam.com/ja-jp'
FIND = (SCRIPTS / 'find_button.js').read_text(encoding='utf-8')
GUARD = (SCRIPTS / 'audio_retry.js').read_text(encoding='utf-8').replace('__VJ_FIND_BUTTON__', FIND)
AUDIO = (SCRIPTS / 'audio_bridge.js').read_text(encoding='utf-8')
HTML = '''<!doctype html><html><body>
<a aria-label="Shazam homepage" href="#home">Shazam</a>
<button id="recognize" aria-label="Tap to Shazam"
 style="position:fixed;right:20px;bottom:20px;width:80px;height:80px">
 <svg width="40" height="40"><defs><symbol id="logo"><rect width="40" height="40"/></symbol></defs>
 <use href="#logo"/></svg></button></body></html>'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chromium', default=None)
    args = parser.parse_args()
    passed = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium, headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--autoplay-policy=no-user-gesture-required'])
        context = browser.new_context(viewport={'width': 1000, 'height': 720})
        context.route('**/*', lambda route: route.abort())

        def fixture():
            page = context.new_page()
            page.set_content(HTML)
            page.evaluate('''() => {
              window.__fixtureLocation = {protocol:'https:', hostname:'www.shazam.com',
                origin:'https://www.shazam.com', pathname:'/ja-jp'};
              window.__messages=[]; window.__nativeCalls=0; window.__siteClicks=0;
              window.chrome=window.chrome||{};
              window.chrome.webview={postMessage:m=>__messages.push(m),addEventListener(){}};
              if (!navigator.mediaDevices) Object.defineProperty(navigator,'mediaDevices',{value:{}});
              navigator.mediaDevices.getUserMedia=async()=>{__nativeCalls++;throw Error('native mic forbidden')};
            }''')
            # Pass a lexical, test-only location into otherwise unchanged scripts.
            for script in (GUARD, AUDIO):
                page.evaluate('() => { const location=window.__fixtureLocation; ' + script + ' }')
            page.evaluate('''async id => {
              await __vjAudioBridge.startLive(48000,id);
              __vjAudioBridge.pushPcm16(btoa('\u0001\u0000'.repeat(7000)),id);
              document.getElementById('recognize').addEventListener('click',()=>__siteClicks++);
            }''', ID)
            button = page.evaluate(FIND)
            assert button and button['idleConfirmed'], button
            page.mouse.click(button['x'], button['y'])
            assert page.evaluate('__siteClicks') == 1
            return page, button

        def probe(page, button, method='probe'):
            return page.evaluate('(a)=>__vjAudioRetry[a[0]](a[1],a[2],a[3])',
                                 [method, ID, HOME, button['key']])

        def click(page, button):
            page.mouse.click(button['x'], button['y'])

        def run(name, case):
            page, button = fixture()
            try:
                case(page, button)
                assert page.evaluate('__nativeCalls') == 0
                passed.append(name)
                print('PASS:', name)
            finally:
                page.close()

        def allowed(page, button):
            before = page.evaluate('__vjAudioBridge.getDiagnostics()')
            assert probe(page, button)['allowed']
            assert page.evaluate('__siteClicks') == 1
            assert page.evaluate('__vjAudioBridge.getDiagnostics()') == before
        run('read-only probe accepts an explicit idle action without changing audio/click state', allowed)

        def deliver(page, button):
            assert probe(page, button, 'arm')['allowed']
            click(page, button)
            assert page.evaluate('__siteClicks') == 2
            result = page.evaluate('(id)=>__vjAudioRetry.finish(id)', ID)
            assert result['outcome'] == 'delivered', result
            assert page.evaluate('__vjAudioBridge.getDiagnostics().getUserMediaCalls') == 0
        run('one guarded trusted CDP-style click reaches the actionable SVG parent', deliver)

        def denied(name, mutation, expected=None, unavailable=False, expected_clicks=1):
            def case(page, button):
                page.evaluate(mutation)
                result = probe(page, button)
                assert not result['allowed'], result
                if expected: assert result['reason'] == expected, result
                if unavailable: assert page.evaluate(FIND) is None
                assert not probe(page, button, 'arm')['allowed']
                assert page.evaluate('__siteClicks') == expected_clicks
            run(name, case)

        denied('generic static Japanese recognition label is not proof of idleness',
               "document.getElementById('recognize').setAttribute('aria-label','Shazam\\u306e\\u97f3\\u697d\\u8a8d\\u8b58\\u30dc\\u30bf\\u30f3')", 'button-changed')
        def generic_initial(page, button):
            page.evaluate("document.getElementById('recognize').setAttribute('aria-label','Shazam\\u306e\\u97f3\\u697d\\u8a8d\\u8b58\\u30dc\\u30bf\\u30f3')")
            generic = page.evaluate(FIND)
            assert generic and not generic['idleConfirmed']
            result = probe(page, generic)
            assert result['reason'] == 'idle-not-proven', result
        run('generic initial button remains usable, but retry is skipped', generic_initial)
        denied('disabled button is not an initial or retry target',
               "document.getElementById('recognize').disabled=true", 'button-unavailable', True)
        denied('aria-disabled button is not clicked',
               "document.getElementById('recognize').setAttribute('aria-disabled','true')", 'button-unavailable', True)
        denied('aria-busy button is not clicked',
               "document.getElementById('recognize').setAttribute('aria-busy','true')", 'button-unavailable', True)
        denied('pressed toggle is not mistaken for an idle recognition button',
               "document.getElementById('recognize').setAttribute('aria-pressed','true')", 'button-unavailable', True)
        denied('listening state overrides a stale Tap to Shazam label',
               "document.getElementById('recognize').setAttribute('data-state','listening')", 'button-unavailable', True)
        denied('unknown transition state does not permit a retry',
               "document.getElementById('recognize').setAttribute('data-state','transitioning')", 'idle-not-proven')
        denied('inert ancestor prevents targeting',
               "document.body.setAttribute('inert','')", 'button-unavailable', True)
        denied('pointer-events none prevents targeting',
               "document.getElementById('recognize').style.pointerEvents='none'", 'button-unavailable', True)
        denied('covered button is not clicked through an overlay',
               "document.body.insertAdjacentHTML('beforeend','<div style=\"position:fixed;inset:0;z-index:9999;background:white\"></div>')", 'button-unavailable', True)
        denied('visible modal elsewhere also blocks a retry',
               "document.body.insertAdjacentHTML('beforeend','<div role=\"dialog\" style=\"width:100px;height:100px\">Consent</div>')", 'busy-or-modal')
        denied('visible recognizing status blocks a retry',
               "document.body.insertAdjacentHTML('beforeend','<div role=\"status\">Listening for music</div>')", 'busy-or-modal')
        denied('small 16px busy indicator inside the button blocks targeting',
               "document.getElementById('recognize').insertAdjacentHTML('beforeend','<span aria-busy=\"true\" style=\"display:block;width:16px;height:16px\"></span>')", 'button-unavailable', True)
        denied('stale or replaced recognition cycle is rejected',
               "__vjAudioBridge.startLive(48000,'b'.repeat(32))", 'bridge-or-cycle-changed')
        denied('stopped audio bridge is rejected', "__vjAudioBridge.stop()", 'bridge-or-cycle-changed')
        denied('overwritten microphone hook is rejected',
               "navigator.mediaDevices.getUserMedia=async()=>({})", 'bridge-or-cycle-changed')
        denied('missing PCM evidence is rejected',
               "__vjAudioBridge.startLive(48000,'a'.repeat(32))", 'audio-not-ready')
        denied('result-page navigation prevents a retry',
               "__fixtureLocation.pathname='/ja-jp/song/1234567/test'", 'not-home')
        denied('non-Shazam origin prevents a retry',
               "__fixtureLocation.origin='https://example.invalid'", 'not-home')
        denied('a successful microphone request prevents a retry',
               "navigator.mediaDevices.getUserMedia({audio:true})", 'audio-already-requested')
        denied('a failed microphone request also prevents a retry',
               "navigator.mediaDevices.getUserMedia({audio:true,video:true}).catch(()=>{})", 'audio-already-requested')
        denied('extra manual clicks prevent another automatic click',
               "document.getElementById('recognize').click()", 'unexpected-click-count', expected_clicks=2)

        def idle_state(page, button):
            page.evaluate("document.getElementById('recognize').setAttribute('aria-label','Shazam music recognition button');document.getElementById('recognize').setAttribute('data-recognition-state','idle')")
            state_button = page.evaluate(FIND)
            assert state_button['stateReason'] == 'explicit-idle-state'
            assert probe(page, state_button)['allowed']
        run('explicit idle recognition state supports otherwise generic button labels', idle_state)

        def race(name, mutation, expected, between_down_up=False):
            def case(page, button):
                assert probe(page, button, 'arm')['allowed']
                if between_down_up:
                    page.mouse.move(button['x'],button['y']); page.mouse.down()
                page.evaluate(mutation)
                if between_down_up: page.mouse.up()
                else: click(page, button)
                assert page.evaluate('__siteClicks') == 1
                result = page.evaluate('(id)=>__vjAudioRetry.finish(id)', ID)
                assert result['outcome'] == 'blocked' and result['reason'] == expected, result
            run(name, case)
        race('audio starts after probe: capture guard suppresses the second click',
             'navigator.mediaDevices.getUserMedia({audio:true})', 'audio-already-requested')
        race('audio starts between mouse down/up: click does not stop recognition',
             'navigator.mediaDevices.getUserMedia({audio:true})', 'audio-already-requested', True)
        race('a pending getUserMedia is rejected even before its promise resolves',
             "() => { AudioContext.prototype.resume=()=>new Promise(()=>{}); void navigator.mediaDevices.getUserMedia({audio:true}); }",
             'audio-already-requested')
        race('button becomes busy after arming: retry is blocked',
             "document.getElementById('recognize').setAttribute('aria-busy','true')", 'button-unavailable')
        race('modal appears after arming: retry is blocked',
             "document.body.insertAdjacentHTML('beforeend','<div role=\"dialog\" style=\"width:100px;height:100px\">Consent</div>')", 'busy-or-modal')
        race('navigation after arming: retry is blocked',
             "__fixtureLocation.pathname='/ja-jp/song/1234567/test'", 'not-home')
        race('cycle cancellation after arming: retry is blocked',
             "__vjAudioRetry.cancel('a'.repeat(32))", 'host-cancelled')
        race('expired guard blocks delayed input rather than allowing it through',
             "() => { const expired=performance.now()+2500;Object.defineProperty(performance,'now',{value:()=>expired}); }", 'guard-expired')
        race('layout shift after arming cannot click a different control',
             "document.getElementById('recognize').style.right='200px'", 'click-target-changed')

        def one_only(page, button):
            assert probe(page, button,'arm')['allowed']
            click(page,button); click(page,button)
            assert page.evaluate('__siteClicks') == 2
            assert page.evaluate('__vjAudioRetry.getDiagnostics().blocked') == 'retry-already-delivered'
        run('an armed retry guard permits no second delivery', one_only)

        def cleanup(page, button):
            assert probe(page,button,'arm')['allowed']
            page.evaluate('(id)=>__vjAudioRetry.cancel(id)', ID)
            page.evaluate('(id)=>__vjAudioRetry.finish(id)', ID)
            click(page,button)
            assert page.evaluate('__siteClicks') == 2
            assert not page.evaluate('__vjAudioRetry.getDiagnostics().armed')
        run('cleanup does not leave normal future clicks blocked', cleanup)

        def native_audio(page, button):
            assert probe(page,button,'arm')['allowed']
            page.evaluate("document.getElementById('recognize').addEventListener('click',()=>{void navigator.mediaDevices.getUserMedia({audio:true})})")
            click(page,button)
            page.wait_for_function('__vjAudioBridge.getDiagnostics().getUserMediaSuccesses===1')
            assert page.evaluate('__vjAudioBridge.getDiagnostics().getUserMediaCalls') == 1
            assert page.evaluate('__messages.some(m=>m.type==="audio-requested")')
            assert page.evaluate('__messages.some(m=>m.type==="audio-stream-started")')
        run('guarded click can start the actual supplied-audio bridge with no native mic', native_audio)
        browser.close()
    print(f'{len(passed)} offline Chromium audio-start checks passed.')


if __name__ == '__main__':
    main()
