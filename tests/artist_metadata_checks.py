"""Offline Chromium tests for identity-bound Shazam primary metadata.

Run: python tests/artist_metadata_checks.py --chromium /path/to/chromium
Test-only dependency: playwright and a Chromium browser. No live service calls.
The included HTML fixtures contain the primary header and JSON-LD copied from
both user-supplied saved pages; generated hash suffixes are not fixed selectors.
"""
import argparse
import html
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'native/ShazamWebViewBridge/Scripts'
FIXTURES = ROOT / 'tests/fixtures/shazam_structural'
URL = 'https://www.shazam.com/ja-jp/track/40820351/orange'
PROMO = 'Apple Music \u306b\u63a5\u7d9a\u3057\u3066\u3001Shazam \u5185\u3067\u66f2\u5168\u4f53\u3092\u30d5\u30eb\u518d\u751f\u3057\u307e\u3057\u3087\u3046\u3002'


def ld(record):
    return '<script type="application/ld+json">' + json.dumps(record, ensure_ascii=True) + '</script>'


def hero(title='Orange', artist='Fixture Artist', apple=''):
    # CSS hashes are intentionally different from the supplied pages.
    return ('<div class="NewTrackPageHeader_trackContent__TEST">'
            '<div class="NewTrackPageHeader_trackTitle__TEST">' + html.escape(title) + '</div>'
            '<a data-test-id="track_userevent_artist_link" href="https://www.shazam.com/artist/a/123456789">'
            + html.escape(artist) + '</a>' + (('<a data-test-id="track_userevent_redirect_apple_music" href="'
            + html.escape(apple, quote=True) + '">Open in Apple Music</a>') if apple else '') + '</div>')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chromium', default=None)
    args = parser.parse_args()
    helper = (SCRIPTS / 'metadata_fields.js').read_text(encoding='utf-8')
    route_script = (SCRIPTS / 'route_evidence.js').read_text(encoding='utf-8')
    observer = (SCRIPTS / 'result_observer.js').read_text(encoding='utf-8')
    extract = '(location) => {' + helper + '\nreturn (\n' + route_script.strip().rstrip(';') + '\n);}'
    loc = {'href': URL, 'hostname': 'www.shazam.com', 'protocol': 'https:'}
    passed = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium, headless=True,
                                     args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context()
        context.route('**/*', lambda r: r.abort())
        page = context.new_page()

        def check(name, body, artist='', evidence='route-only', title=None, canonical=URL):
            page.set_content('<html><head><base href="' + URL + '"><link rel="canonical" href="'
                             + canonical + '"></head><body><main>' + body + '</main></body></html>')
            value = page.evaluate(extract, loc)
            assert value and value['artist'] == artist and value['evidence'] == evidence, (name, value)
            if title is not None:
                assert value['title'] == title, (name, value)
            passed.append(name)
            return value

        primary = {'@type': 'MusicRecording', 'name': 'Orange', 'url': URL,
                   'byArtist': {'name': 'Fixture Artist'}}
        generic = '<section><h1>Orange</h1><span data-testid="artist-name">Fixture Artist</span></section>'
        cases = [
            ('generic h1 and artist marker', generic),
            ('generic h2 and Shazam artist link', '<section><h2>Orange</h2><a href="/artist/a/123456789">A</a></section>'),
            ('generic div and artist marker', '<section><div>Orange</div><span data-testid="track-subtitle">A</span></section>'),
            ('arbitrary result dialog', '<dialog open><h1>Orange</h1><span data-testid="artist-name">A</span></dialog>'),
            ('homepage-style unlisted caption', '<div><h2>Unlisted new promotion 123</h2><a href="/artist/a/123456789">A</a></div>'),
            ('footer artist field', '<footer>' + generic + '</footer>'),
            ('nearby CTA', '<div>Orange<p>' + PROMO + '</p></div>'),
        ]
        for name, body in cases:
            check(name + ' cannot supply metadata', body, title='')
        check('header without structured identity cannot supply metadata', hero(), title='')
        check('exact primary JSON-LD fallback', ld(primary), 'Fixture Artist', 'jsonld', 'Orange')
        check('title-only JSON-LD without identity rejected', ld({k: v for k, v in primary.items() if k != 'url'}))
        check('different track ID rejected', ld(dict(primary, url='https://www.shazam.com/track/99999999/orange')))
        check('different route kind rejected', ld(dict(primary, url='https://www.shazam.com/song/40820351/orange')))
        check('canonical from previous SPA route rejected', ld(primary) + hero(), canonical='https://www.shazam.com/track/99999999/old')
        check('ItemList recommendations not traversed', ld({'@type': 'ItemList', 'itemListElement': [primary]}))
        check('citation recommendations not traversed', ld({'@type': 'WebPage', 'citation': [primary]}))
        check('creator is not substituted for byArtist', ld(dict(primary, byArtist=None, creator={'name': 'Wrong'})), evidence='jsonld')
        check('other artist region cannot fill byArtist', ld(dict(primary, byArtist=None)) + generic, evidence='jsonld')
        check('conflicting primary records rejected', ld([primary, dict(primary, byArtist={'name': 'Wrong'})]))
        check('JSON-LD array artists', ld(dict(primary, byArtist=['Artist A', {'name': 'Artist B'}])), 'Artist A, Artist B', 'jsonld')
        check('WebPage mainEntity record', ld({'@type': 'WebPage', 'mainEntity': primary}), 'Fixture Artist', 'jsonld')
        check('JSON-LD CTA artist still rejected', ld(dict(primary, byArtist={'name': PROMO})), evidence='jsonld')
        check('bound header used', ld(primary) + hero(), 'Fixture Artist', 'track-primary-header', 'Orange')
        check('header preserves coartist omitted by JSON-LD', ld(primary) + hero(artist='Fixture Artist & Another Artist'),
              'Fixture Artist & Another Artist', 'track-primary-header')
        check('hash suffix independent', ld(primary) + hero().replace('__TEST', '__DifferentHash99'),
              'Fixture Artist', 'track-primary-header')
        check('old title under new route fails closed', ld(primary) + hero(title='Previous song'))
        check('different primary headers fail closed', ld(primary) + hero() + hero(artist='Wrong'))
        check('punctuation artist preserved inside bound header', ld(primary) + hero(artist='!!!'), '!!!', 'track-primary-header')
        check('unlisted title is not filtered by its words', ld(dict(primary, name='Unlisted new promotion 123')) + hero(title='Unlisted new promotion 123'),
              'Fixture Artist', 'track-primary-header', 'Unlisted new promotion 123')
        outside = '<a href="https://music.apple.com/jp/album/x/123456789?i=987654321">Unrelated Apple link</a>'
        value = check('outside Apple ID not attached', ld(primary) + hero() + outside, 'Fixture Artist', 'track-primary-header')
        assert value['appleTrackId'] == ''
        value = check('dedicated Apple link attached', ld(primary) + hero(apple='https://music.apple.com/jp/album/x/123456789?i=876543210'),
                      'Fixture Artist', 'track-primary-header')
        assert value['appleTrackId'] == '876543210'
        for metadata_path in sorted(FIXTURES.glob('*.json')):
            meta = json.loads(metadata_path.read_text(encoding='utf-8'))
            page.set_content(metadata_path.with_suffix('.html').read_text(encoding='utf-8'))
            value = page.evaluate(extract, dict(loc, href=meta['url']))
            assert value['title'] == meta['title'] and value['artist'] == meta['artist'], (meta, value)
            assert value['evidence'] == 'track-primary-header', value
            assert value['appleTrackId'], value
            print('SAMPLE:', metadata_path.stem, json.dumps(value, ensure_ascii=False))
            passed.append('saved sample ' + metadata_path.stem + ' exact title and full artist')

        # Current recognition on the home page must NOT finish because a new DOM
        # heading/artist list appeared. Keep the same armed cycle for real results.
        page.close()
        page = context.new_page()
        page.set_content('<html><head><base href="https://www.shazam.com/ja-jp/"></head><body></body></html>')
        page.evaluate('window.__messages=[];window.chrome=window.chrome||{};window.chrome.webview={postMessage:x=>__messages.push(x)}')
        home = dict(loc, href='https://www.shazam.com/ja-jp')
        page.evaluate('(location) => {' + helper + '\n' + observer + '}', home)
        cycle = 'a' * 32
        page.evaluate('(id)=>__vjResults.arm(id)', cycle)
        for title in ['\u30c7\u30a3\u30b9\u30ab\u30d0\u30ea\u30fc \u65e5\u672c \u306e\u30c8\u30e9\u30c3\u30af',
                      '\u6ce8\u76ee\u306e\u30c8\u30c3\u30d7\u30a2\u30fc\u30c6\u30a3\u30b9\u30c8', 'Never-seen-before homepage caption']:
            markup = '<section><h2>' + html.escape(title) + '</h2><a href="https://www.shazam.com/artist/a/123456789">A</a>' + outside + '</section>'
            page.evaluate('(x)=>document.body.insertAdjacentHTML("beforeend",x)', markup)
            page.evaluate('__vjResults.scan()')
            assert not page.evaluate('__messages.filter(m=>m.type==="candidate")')
            passed.append('new home heading ignored regardless of wording: ' + title)
        page.evaluate('(x)=>document.body.insertAdjacentHTML("beforeend",x)', '<dialog open>' + generic + '</dialog>')
        page.evaluate('__vjResults.scan()')
        assert not page.evaluate('__messages.filter(m=>m.type==="candidate")')
        passed.append('new arbitrary dialog ignored after arm')
        track = {'matches': [{}], 'track': {'title': 'Orange', 'subtitle': 'Fixture Artist', 'url': URL}}
        page.evaluate('(p)=>__vjResults.inspectRecognition(p,"a".repeat(32))', track)
        assert page.evaluate('__messages.filter(m=>m.type==="candidate").at(-1).title') == 'Orange'
        passed.append('same cycle still accepts actual recognition response after homepage mutation')
        page.evaluate('__messages=[];__vjResults.arm("b".repeat(32))')
        page.evaluate('(p)=>__vjResults.inspectRecognition(p,"a".repeat(32))', track)
        assert not page.evaluate('__messages')
        passed.append('stale request response ignored')
        page.evaluate('(p)=>__vjResults.inspectRecognition(p,"b".repeat(32))', {'track': track['track']})
        assert not page.evaluate('__messages')
        passed.append('catalog record without matches ignored')
        browser.close()
    for name in passed:
        print('PASS:', name)
    print(f'{len(passed)} Chromium structural/artist fixture checks passed.')


if __name__ == '__main__':
    main()
