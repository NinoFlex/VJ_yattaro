"""Offline browser checks of the actual native readiness JavaScript expression.

Uses about:blank/set_content, fake bridge interfaces, and no network/navigation.
Does not execute C#, WebView2, physical audio, or the official Shazam service.
"""
import argparse
import json
from pathlib import Path
import re
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'native/ShazamWebViewBridge/Scripts'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chromium', default=None)
    args = parser.parse_args()
    native = (ROOT / 'native/ShazamWebViewBridge/BridgeHost.cs').read_text(encoding='utf-8')
    find_once = native.split('private async Task<Button?> FindButtonOnceAsync', 1)[1]
    expression = find_once.split('var script =', 1)[1].split('var json =', 1)[0]
    pieces = re.findall(r'Script\("find_button\.js"\)|"(?:[^"\\]|\\.)*"', expression)
    readiness = ''.join((SCRIPTS / 'find_button.js').read_text() if x.startswith('Script(')
                        else json.loads(x) for x in pieces)
    html = ('<html><body><h1>Unrelated home heading</h1>'
            '<button aria-label="Tap to Shazam" '
            'style="position:fixed;right:20px;bottom:20px;width:80px;height:80px">'
            'Shazam</button></body></html>')
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium, headless=True,
                                     args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context(viewport={'width': 1000, 'height': 720})
        context.route('**/*', lambda r: r.abort())
        page = context.new_page(); page.set_content(html)
        assert page.evaluate(readiness) is None
        checks.append('visible button alone is not page readiness')
        page.evaluate('''() => {
          window.__called=0;
          window.__vjAudioBridge={startLive(){__called++},stop(){__called++}};
          window.__vjResults={arm(){__called++},stop(){__called++}};
          window.__vjTrackMetadata={readPrimaryTrack(){__called++}};
        }''')
        result = page.evaluate(readiness)
        assert result and result['x'] > 850, result
        checks.append('actual C#-embedded JS expression returns a ready live button')
        assert page.evaluate('__called') == 0
        checks.append('checking readiness never arms/starts/stops the interfaces')
        for object_name, method in [('__vjAudioBridge', 'startLive'), ('__vjAudioBridge', 'stop'),
                                    ('__vjResults', 'arm'), ('__vjResults', 'stop'),
                                    ('__vjTrackMetadata', 'readPrimaryTrack')]:
            page.evaluate('(args)=>{window.__saved=window[args[0]][args[1]];delete window[args[0]][args[1]]}',
                          [object_name, method])
            assert page.evaluate(readiness) is None
            page.evaluate('(args)=>{window[args[0]][args[1]]=window.__saved}', [object_name, method])
            checks.append('missing ' + object_name + '.' + method + ' prevents readiness')
        page.evaluate('''() => {const d=document.createElement('div');d.id='overlay';
            d.style='position:fixed;inset:0;z-index:100000;background:white';document.body.append(d)}''')
        assert page.evaluate(readiness) is None
        checks.append('covered button is not considered ready')
        browser.close()
    for message in checks: print('PASS: ' + message)
    print(f'{len(checks)} offline Chromium readiness checks passed.')


if __name__ == '__main__':
    main()
