"""Offline real Chromium checks for linked and unlinked primary artists.
No external requests; full uploaded HTML can be checked without bundling it.
"""
import argparse
import html
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]
URL = 'https://www.shazam.com/track/987654321/fixture-song'
CAN = 'https://www.shazam.com/track/987654321/unavailable'

def hero(title='Fixture Song', artist='Fixture Artist'):
    return ('<div class="NewTrackPageHeader_trackContent__T">'
            '<div class="NewTrackPageHeader_trackTitle__T">'+html.escape(title)+'</div>'
            '<div class="TrackPageArtistLink_artistData__T">'
            '<div class="TrackPageArtistLink_artistLink__T">'
            '<span class="TrackPageArtistLink_artistNameText__T">'+html.escape(artist)+'</span>'
            '</div></div></div>')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-root',type=Path,default=ROOT)
    parser.add_argument('--chromium',default='/usr/bin/chromium')
    parser.add_argument('--saved-pages',type=Path)
    args=parser.parse_args()
    js=(args.source_root/'native/ShazamWebViewBridge/Scripts/metadata_fields.js').read_text()
    evaluate='location=>{'+js+';return __vjTrackMetadata.readPrimaryTrack(location.href);}'
    passed=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox'])
        ctx=browser.new_context(); ctx.route('**/*', lambda r:r.abort()); page=ctx.new_page()
        def check(name,body=None, url=URL,canonical=CAN, expected=('Fixture Song','Fixture Artist'),head=''):
            page.set_content('<html><head><base href="https://www.shazam.com/"><link rel="canonical" href="'+canonical+'">'+head+'</head><body>'+(hero() if body is None else body)+'</body></html>')
            v=page.evaluate(evaluate,dict(href=url,hostname='www.shazam.com',protocol='https:'))
            assert (v['title'],v['artist'])==expected,(name,v)
            passed.append(name)
        for label in ['ohayou','we_were_lovers','sentimental','flyaway']:
            meta=json.loads((ROOT/f'tests/fixtures/shazam_no_jsonld/{label}.json').read_text())
            page.set_content((ROOT/f'tests/fixtures/shazam_no_jsonld/{label}.html').read_text())
            v=page.evaluate(evaluate,dict(href=meta['url'],hostname='www.shazam.com',protocol='https:'))
            assert (v['title'],v['artist'])==(meta['title'],meta['artist']),(label,v)
            passed.append(label); print('SAMPLE',label,json.dumps(v,ensure_ascii=False))
            if args.saved_pages:
                original=args.saved_pages/(label+'.html')
                if original.exists():
                    page.set_content(original.read_text())
                    v=page.evaluate(evaluate,dict(href=meta['url'],hostname='www.shazam.com',protocol='https:'))
                    assert (v['title'],v['artist'])==(meta['title'],meta['artist']),(label,v)
                    passed.append('full saved '+label)
        check('arbitrary identity no special case')
        for title,artist in [('Another Tune','Independent Artist'),('第三の曲','別の歌手'),('Rock & Roll','One & Two')]:
            check('arbitrary '+title,hero(title,artist),url=CAN,expected=(title,artist))
        check('nonlink text hidden',hero().replace('<span ','<span style="display:none" '),expected=('',''))
        check('promo rejected',hero(artist='Listen on Apple Music'),expected=('',''))
        check('outside artist region',hero().replace('TrackPageArtistLink_artistData__T','Other'),expected=('',''))
        check('generic nearby label',hero().replace('TrackPageArtistLink_artistNameText__T','Other'),expected=('',''))
        check('different ID',canonical=CAN.replace('987654321','888888888'),expected=('',''))
        check('different namespace',canonical=CAN.replace('/track/','/song/'),expected=('',''))
        check('homepage canonical',canonical='https://www.shazam.com/',expected=('',''))
        check('stale title retained guard',hero(title='Previous Song'),expected=('',''))
        check('multiple headers',hero()+hero(),expected=('',''))
        check('inside footer', '<footer>'+hero()+'</footer>',expected=('',''))
        check('artist span in footer',hero().replace('<span','<footer><span').replace('</span>','</span></footer>'),expected=('',''))
        for href in ['https://example.invalid/artist/a/123456789','/song/123456789/no']:
            body=hero().replace('<span','<a href="'+href+'"><span').replace('</span>','</span></a>')
            check('invalid link cannot fall through '+href,body,expected=('',''))
        check('broken jsonld cannot fall through',head='<script type="application/ld+json">{bad}</script>',expected=('',''))
        browser.close()
    print('PASS',len(passed),'plain artist/browser checks')
if __name__=='__main__':main()
