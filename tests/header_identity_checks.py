"""Offline real-DOM tests for the no-JSON-LD primary-header fallback.

python tests/header_identity_checks.py --chromium /path/to/chromium
Test-only dependencies: Playwright and Chromium. All network requests aborted.
Fixtures contain only the canonical and primary header from user-saved HTML;
track/artist values in fixtures are assertions, never runtime lookup mappings.
"""
import argparse
import html
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'native/ShazamWebViewBridge/Scripts'
FIXTURES = ROOT / 'tests/fixtures/shazam_no_jsonld'
URL = 'https://www.shazam.com/ja-jp/track/987654321/fixture-song'
CANONICAL = 'https://www.shazam.com/track/987654321/unavailable'
TITLE, ARTIST = 'Fixture Song', 'Fixture Artist'


def hero(title=TITLE, artist=ARTIST, artist_href='/artist/fixture/654321987', extra=''):
    return ('<div class="NewTrackPageHeader_trackContent__TEST">'
            '<div class="NewTrackPageHeader_trackTitle__TEST">' + html.escape(title) + '</div>'
            '<a data-test-id="track_userevent_artist_link" href="' + html.escape(artist_href, quote=True) + '">'
            + html.escape(artist) + '</a>' + extra + '</div>')


def ld(record):
    return '<script type="application/ld+json">' + json.dumps(record) + '</script>'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chromium', default=None)
    parser.add_argument('--source-root', type=Path, default=ROOT,
                        help='Compare the same tests against an unmodified source tree.')
    parser.add_argument('--saved-pages', type=Path, default=None,
                        help='Optional original HTML directory containing sentimental.html/flyaway.html.')
    args = parser.parse_args()
    scripts = args.source_root / 'native/ShazamWebViewBridge/Scripts'
    helper = (scripts / 'metadata_fields.js').read_text(encoding='utf-8')
    observer = (scripts / 'result_observer.js').read_text(encoding='utf-8')
    extract = '(location) => {' + helper + '\nreturn __vjTrackMetadata.readPrimaryTrack(location.href);}'
    passed = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium, headless=True,
                                     args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context()
        context.route('**/*', lambda r: r.abort())
        page = context.new_page()

        def location(url):
            return {'href': url, 'hostname': 'www.shazam.com', 'protocol': 'https:'}

        def check(name, body=None, *, canonical=CANONICAL, url=URL, extra_head='',
                  title='', artist='', evidence='route-only'):
            head = '<base href="https://www.shazam.com/">'
            if canonical is not None:
                head += '<link rel="canonical" href="' + html.escape(canonical, quote=True) + '">'
            page.set_content('<html><head>' + head + extra_head + '</head><body>'
                             + (hero() if body is None else body) + '</body></html>')
            value = page.evaluate(extract, location(url))
            if evidence is None:
                assert value is None, (name, value)
            else:
                assert value is not None, (name, value)
                assert (value['title'], value['artist'], value['evidence']) == (title, artist, evidence), (name, value)
            passed.append(name)
            return value

        accept = dict(title=TITLE, artist=ARTIST, evidence='track-primary-header')
        # Regression reproductions from the supplied saved pages, no HTTP required.
        for label in ['sentimental', 'flyaway']:
            meta = json.loads((FIXTURES / (label + '.json')).read_text(encoding='utf-8'))
            page.set_content((FIXTURES / (label + '.html')).read_text(encoding='utf-8'))
            assert page.locator('script[type="application/ld+json"]').count() == 0
            value = page.evaluate(extract, location(meta['url']))
            assert value['title'] == meta['title'] and value['artist'] == meta['artist'], (label, value)
            assert value['evidence'] == 'track-primary-header' and value['appleTrackId'] == '', value
            print('SAMPLE:', label, json.dumps(value, ensure_ascii=False))
            passed.append('saved primary header: ' + label)
            if args.saved_pages:
                page.set_content((args.saved_pages / (label + '.html')).read_text(encoding='utf-8'))
                value = page.evaluate(extract, location(meta['url']))
                assert (value['title'], value['artist'], value['evidence']) == (
                    meta['title'], meta['artist'], 'track-primary-header'), (label, value)
                passed.append('original full saved HTML: ' + label)

        check('arbitrary new track and artist, no fixed mapping', **accept)
        check('song namespace also supported', url=URL.replace('/track/', '/song/'),
              canonical=CANONICAL.replace('/track/', '/song/'), **accept)
        check('locale and slug differences retain exact identity', canonical=CANONICAL.replace('/track/', '/en-us/track/'), **accept)
        check('unavailable route contains no title hint', url=CANONICAL, **accept)
        check('slugless route contains no title hint', url=CANONICAL.rsplit('/', 1)[0], **accept)
        check('matching repeated canonical identities are unambiguous',
              extra_head='<link rel="canonical" href="' + URL + '">', **accept)
        check('changed CSS hash suffix', hero().replace('__TEST', '__DifferentHash'), **accept)
        check('NFKC and punctuation title normalization', hero(title='Fixture-Song!'),
              title='Fixture-Song!', artist=ARTIST, evidence='track-primary-header')
        check('coartist text preserved', hero(artist='Artist A & Artist B'),
              title=TITLE, artist='Artist A & Artist B', evidence='track-primary-header')
        check('punctuation artist is valid', hero(artist='!!!'),
              title=TITLE, artist='!!!', evidence='track-primary-header')
        value = check('dedicated Apple link only', hero(extra='<a data-test-id="track_userevent_redirect_apple_music" '
                      'href="https://music.apple.com/jp/album/x/111111111?i=222222222">Listen</a>'), **accept)
        assert value['appleTrackId'] == '222222222'
        value = check('outside Apple link not attached', hero() + '<a href="https://music.apple.com/jp/album/x/111111111?i=333333333">Other</a>', **accept)
        assert value['appleTrackId'] == ''
        check('no canonical rejected', canonical=None)
        check('empty canonical rejected', canonical='')
        check('homepage canonical rejected', canonical='https://www.shazam.com/en-us')
        check('previous SPA canonical rejected', canonical=CANONICAL.replace('987654321', '999999999'))
        check('different namespace rejected', canonical=CANONICAL.replace('/track/', '/song/'))
        check('non-Shazam canonical rejected', canonical=CANONICAL.replace('www.shazam.com', 'example.invalid'))
        check('HTTP canonical rejected', canonical=CANONICAL.replace('https:', 'http:'))
        check('conflicting canonical tags rejected', extra_head='<link rel="canonical" href="https://www.shazam.com/track/999999999/unavailable">')
        check('matching og identity accepted', extra_head='<meta property="og:url" content="' + URL + '">', **accept)
        check('conflicting og identity rejected', extra_head='<meta property="og:url" content="https://www.shazam.com/song/999999999/other">')
        check('homepage og rejected', extra_head='<meta property="og:url" content="https://www.shazam.com/">')
        check('homepage cannot yield metadata even with bound header', url='https://www.shazam.com/ja-jp', evidence=None)
        check('old title under new matching canonical rejected', hero(title='Previous Song'))
        check('malformed slug escapes rejected', url=URL.rsplit('/', 1)[0] + '/%E0%A4')
        check('artist missing rejected', hero(artist=''))
        check('artist name without a dedicated link rejected', hero().replace('track_userevent_artist_link', 'unrelated'))
        check('generic adjacent artist text rejected', '<h1>Fixture Song</h1><span>Fixture Artist</span>')
        check('outside dedicated artist link rejected', hero(artist='') + '<a data-test-id="track_userevent_artist_link" href="/artist/a/123456789">Wrong</a>')
        check('untrusted artist link rejected', hero(artist_href='https://example.invalid/artist/a/123456789'))
        check('artist link to wrong page type rejected', hero(artist_href='/song/123456789/wrong'))
        check('promotional artist rejected', hero(artist='Listen on Apple Music'))
        check('hidden header rejected', '<div style="display:none">' + hero() + '</div>')
        check('hidden artist rejected', hero().replace('<a ', '<a style="display:none" '))
        check('different primary headers rejected', hero() + hero(artist='Wrong'))
        check('duplicate visible headers rejected', hero() + hero())
        for tag in ['footer', 'nav', 'aside', 'dialog open', 'div role="dialog"', 'div role="navigation"']:
            check('non-primary region rejected: ' + tag, '<' + tag + '>' + hero() + '</' + tag.split()[0] + '>')
        check('two title fields rejected', hero(extra='<div class="NewTrackPageHeader_trackTitle__OTHER">Other</div>'))
        primary = {'@type': 'MusicRecording', 'url': URL, 'name': TITLE, 'byArtist': {'name': ARTIST}}
        check('existing structured path unchanged', ld(primary) + hero(), **accept)
        check('JSON-LD fallback unchanged', ld(primary), title=TITLE, artist=ARTIST, evidence='jsonld')
        check('malformed JSON-LD not hidden by new fallback', '<script type="application/ld+json">{broken</script>' + hero())
        check('conflicting structured ID cannot fall through', ld(dict(primary, url=URL.replace('987654321', '111111111'))) + hero())
        check('ambiguous structured records cannot fall through', ld([primary, dict(primary, byArtist={'name':'Wrong'})]) + hero())
        check('unbound structured data cannot fall through', ld({'@type':'WebPage'}) + hero())
        # readPrimaryTrack() cannot be used to attach a different route's DOM.
        check('prepare route mismatch fixture', **accept)
        value = page.evaluate('(location) => {' + helper + '\nreturn __vjTrackMetadata.readPrimaryTrack("' + URL + '");}',
                              location('https://www.shazam.com/ja-jp'))
        assert value['evidence'] == 'route-only' and value['artist'] == '', value
        passed.append('current browser route must match the requested identity')

        # Observer end-to-end: canonical update alone must not promote an old title.
        page.close(); page = context.new_page()
        page.set_content('<base href="https://www.shazam.com/"><link rel="canonical" href="' + CANONICAL + '">' + hero())
        page.evaluate('window.__messages=[];window.chrome={webview:{postMessage:m=>__messages.push(m)}}')
        page.evaluate('(location) => { window.__testLocation=location;' + helper + '\n' + observer + '}', location(URL))
        page.evaluate('__vjResults.scan()')
        assert not page.evaluate('__messages')
        passed.append('unarmed observer does not publish a header')
        page.evaluate('__vjResults.arm("a".repeat(32)); __vjResults.scan()')
        item = page.evaluate('__messages.filter(m=>m.type==="candidate").at(-1)')
        assert item['title'] == TITLE and item['artist'] == ARTIST and item['id'] == 'a'*32, item
        passed.append('armed observer publishes the bound header with request ID')
        page.evaluate('__messages=[]; __testLocation.href="https://www.shazam.com/track/111111111/next-song"; __vjResults.scan()')
        assert not page.evaluate('__messages.some(m=>m.type==="candidate" && m.title)')
        passed.append('SPA route change before canonical cannot publish old text')
        page.evaluate('document.querySelector("link").href=__testLocation.href; __vjResults.scan()')
        assert not page.evaluate('__messages.some(m=>m.type==="candidate" && m.title)')
        passed.append('SPA canonical change before header cannot publish old title')
        page.evaluate('document.querySelector("[class*=trackTitle__]").textContent="Next Song"; __vjResults.scan()')
        item=page.evaluate('__messages.filter(m=>m.type==="candidate").at(-1)')
        assert item['title']=='Next Song' and item['shazamTrackId']=='111111111', item
        passed.append('SPA matching new title enables current identity')
        page.evaluate('__vjResults.stop(); __messages=[]; __vjResults.scan()')
        assert not page.evaluate('__messages')
        passed.append('stopped observer cannot publish a late header')
        browser.close()
    for name in passed:
        print('PASS:', name)
    print(f'{len(passed)} Chromium no-JSON-LD header checks passed.')


if __name__ == '__main__':
    main()
