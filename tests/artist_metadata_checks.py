"""Real Chromium DOM fixtures; all requests are intercepted (no live Shazam).

Run: python tests/artist_metadata_checks.py [--chromium /path/to/chromium]
Optional test dependency only: playwright + Chromium. App dependencies unchanged.
"""
import argparse
import html
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'native/ShazamWebViewBridge/Scripts'
URL = 'https://www.shazam.com/ja-jp/track/40820351/orange'
PROMO = 'Apple Music \u306b\u63a5\u7d9a\u3057\u3066\u3001Shazam \u5185\u3067\u66f2\u5168\u4f53\u3092\u30d5\u30eb\u518d\u751f\u3057\u307e\u3057\u3087\u3046\u3002 \u306b\u63a5\u7d9a'
KIT = '\u958b\u767a\u8005\u5411\u3051ShazamKit'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chromium', default=None)
    args = parser.parse_args()
    helper = (SCRIPTS / 'metadata_fields.js').read_text(encoding='utf-8')
    route_script = (SCRIPTS / 'route_evidence.js').read_text(encoding='utf-8').replace('__BASELINE_TRACK_IDS__', '[]')
    extract = '(location) => {' + helper + '\nreturn ' + route_script + '}'
    observer = (SCRIPTS / 'result_observer.js').read_text(encoding='utf-8')
    fixture_location = {'href': URL, 'hostname': 'www.shazam.com', 'protocol': 'https:'}
    passed = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium, headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context()
        context.route('**/*', lambda r: r.fulfill(status=200, content_type='text/html', body='<html><body></body></html>'))
        page = context.new_page()
        # No browser navigation. Use generated markup in about:blank with a
        # lexical location fixture; only the DOM/layout APIs are real Chromium.

        def check(name, body, artist, evidence=None, title=None):
            page.set_content('<html><head><base href="' + URL + '"></head><body><main>' + body + '</main></body></html>')
            value = page.evaluate(extract, fixture_location)
            assert value is not None, name
            assert value['artist'] == artist, (name, value)
            if evidence is not None:
                assert value['evidence'] == evidence, (name, value)
            if title is not None:
                assert value['title'] == title, (name, value)
            passed.append(name)
            return value

        for noise in (PROMO, KIT, 'An unrelated caption not in any blocklist'):
            check('no position guessing: ' + noise, '<div><div>Orange</div><p>' + html.escape(noise) + '</p></div>', '', 'route-only')
        check('semantic heading plus CTA is incomplete', '<section><h1>Orange</h1><p>' + PROMO + '</p></section>', '', 'track-heading', 'Orange')
        check('wildcard connect-subtitle no longer accepted', '<section><h1>Orange</h1><div data-testid="connect-subtitle">A harmless-looking unrelated caption</div></section>', '')
        check('exact artist field', '<section><h1>Orange</h1><span data-testid="artist-name">Fixture Artist</span></section>', 'Fixture Artist', 'track-heading')
        check('exact track subtitle field beats closer CTA', '<section><h1>Orange</h1><p>' + PROMO + '</p><span data-testid="track-subtitle">Fixture Artist</span></section>', 'Fixture Artist')
        check('explicit field also validates its contents', '<section><h1>Orange</h1><span data-testid="track-subtitle">' + PROMO + '</span></section>', '')
        check('plain title plus explicit artist', '<section><div>Orange</div><span data-testid="track-subtitle">Fixture Artist</span></section>', 'Fixture Artist', 'route-slug-artist')
        check('local Shazam artist link', '<section><h1>Orange</h1><a href="https://www.shazam.com/ja-jp/artist/fixture/123456789">Fixture Artist</a></section>', 'Fixture Artist')
        check('Apple artist link only', '<section><h1>Orange</h1><a href="https://music.apple.com/jp/artist/fixture/123456789">Fixture Artist</a></section>', 'Fixture Artist')
        check('foreign artist URL is not a Shazam field', '<section><h1>Orange</h1><a href="https://example.test/artist/fixture">Wrong Artist</a></section>', '')
        check('query-string artist text is not an artist URL', '<section><h1>Orange</h1><a href="https://www.shazam.com/help?next=/artist/test">Wrong Artist</a></section>', '')
        check('recommendation artist cannot fill missing artist', '<section><h1>Orange</h1></section><section><h2>Other Song</h2><a href="/artist/other/123456789">Wrong Artist</a></section>', '')
        check('main is not a global fallback region', '<h1>Orange</h1><a href="/artist/other/123456789">Wrong Artist</a>', '')
        check('footer ignored even with exact artist marker', '<section><h1>Orange</h1><footer><span data-testid="artist-name">Wrong Artist</span></footer></section>', '')
        check('hidden artist ignored', '<section><h1>Orange</h1><span data-testid="artist-name" hidden>Wrong Artist</span></section>', '')
        check('conflicting explicit fields are not guessed', '<section><h1>Orange</h1><span data-testid="artist-name">One</span><span data-testid="track-subtitle">Two</span></section>', '')
        check('artist field preserves punctuation/numbers', '<section><h1>Orange</h1><span data-testid="artist-name">!!!</span></section>', '!!!')
        check('multiple artists within one field', '<section><h1>Orange</h1><span data-testid="track-subtitle"><a href="/artist/a/123456789">Artist A</a> &amp; <a href="/artist/b/223456789">Artist B</a></span></section>', 'Artist A, Artist B')

        def ld(value):
            return '<script type="application/ld+json">' + json.dumps(value, ensure_ascii=True) + '</script>'

        primary = {'@type': 'MusicRecording', 'name': 'Orange', 'url': URL, 'byArtist': {'name': 'Fixture Artist'}}
        check('exact JSON-LD', '<section><h1>Orange</h1><p>' + PROMO + '</p></section>' + ld(primary), 'Fixture Artist', 'jsonld')
        check('title-matched primary JSON-LD without URL', ld({k: v for k, v in primary.items() if k != 'url'}), 'Fixture Artist', 'jsonld')
        wrong = dict(primary, url='https://www.shazam.com/ja-jp/track/99999999/orange')
        check('same title with different track ID is rejected', ld(wrong), '')
        check('recommendation ItemList is not traversed', ld({'@type': 'ItemList', 'itemListElement': [primary]}), '')
        check('JSON-LD artist never borrowed from another region', ld(dict(primary, byArtist=None)) + '<section><h2>Other</h2><a href="/artist/other/123456789">Wrong Artist</a></section>', '')
        check('JSON-LD CTA artist is rejected', ld(dict(primary, byArtist={'name': PROMO})), '')
        check('JSON-LD creator is not the performer', ld(dict(primary, byArtist=None, creator={'name': 'Wrong Artist'})), '')
        check('conflicting structured records are rejected', ld([primary, dict(primary, byArtist={'name': 'Wrong Artist'})]), '')
        check('string and array byArtist are supported', ld(dict(primary, byArtist=['Artist A', {'name': 'Artist B'}])), 'Artist A, Artist B')
        check('WebPage mainEntity is supported', ld({'@type': 'WebPage', 'mainEntity': primary}), 'Fixture Artist')
        check('Apple link outside track region is not attached', '<section><h1>Orange</h1><span data-testid="artist-name">Fixture Artist</span></section><section><a href="https://music.apple.com/jp/album/other/123456789?i=999999999">Other</a></section>', 'Fixture Artist')
        value = page.evaluate(extract, fixture_location)
        assert value['appleTrackId'] == '', value

        # Exercise production result_observer.js with a real DOM and intercepted
        # network. New dialogs and delayed artist rendering must still work.
        page.set_content('<html><head><base href="' + URL + '"></head><body></body></html>')
        page.evaluate("window.__messages=[];window.chrome=window.chrome||{};window.chrome.webview={postMessage:x=>window.__messages.push(x)}")
        page.evaluate('(location) => {' + helper + '\n' + observer + '}', dict(fixture_location, href='https://www.shazam.com/ja-jp'))
        page.evaluate("__vjResults.arm('a'.repeat(32))")
        page.evaluate('(text)=>{document.body.innerHTML=`<section><h1>Orange</h1><span data-testid="connect-subtitle">${text}</span></section>`}', PROMO)
        page.evaluate('__vjResults.scan()')
        assert not page.evaluate('__messages.filter(m=>m.type==="candidate")')
        passed.append('result observer does not publish an advertisement')
        page.evaluate("document.querySelector('section').insertAdjacentHTML('beforeend','<span data-testid=\"artist-name\">Fixture Artist</span>')")
        page.evaluate('__vjResults.scan()')
        assert page.evaluate('__messages.filter(m=>m.type==="candidate").at(-1).artist') == 'Fixture Artist'
        passed.append('delayed explicit artist publishes a valid result')
        page.evaluate("__vjResults.arm('b'.repeat(32));__messages=[]")
        payload = {'matches': [{}], 'track': {'title': 'Orange', 'subtitle': PROMO, 'url': URL}}
        page.evaluate('(p)=>__vjResults.inspectRecognition(p,"b".repeat(32))', payload)
        assert not page.evaluate('__messages.filter(m=>m.type==="candidate")')
        passed.append('recognition-response UI artist is rejected')
        payload['track']['subtitle'] = 'Lefties Soul Connection'
        page.evaluate('(p)=>__vjResults.inspectRecognition(p,"b".repeat(32))', payload)
        assert page.evaluate('__messages.filter(m=>m.type==="candidate").at(-1).artist') == 'Lefties Soul Connection'
        passed.append('valid artist containing Connection is preserved')
        browser.close()
    for name in passed:
        print('PASS:', name)
    print(f'{len(passed)} Chromium artist-metadata fixture checks passed.')


if __name__ == '__main__':
    main()
