from html.parser import HTMLParser
from pathlib import Path

path = Path('/app/app/static/index.html')
text = path.read_text(encoding='utf-8')

class SrcdocExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.srcdoc = None
    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'iframe':
            d = dict(attrs)
            if d.get('id') == 'codex-visualization' and 'data-srcdoc' in d:
                self.srcdoc = d['data-srcdoc']

p = SrcdocExtractor()
p.feed(text)
if not p.srcdoc:
    raise RuntimeError('Could not find embedded dashboard srcdoc')

inner = p.srcdoc
inner = inner.replace('Prototype mode<br>Free public/EOD data architecture<br>No trade recommendation', 'Production mode<br>Zerodha-connected backtest<br>Research only')
inner = inner.replace('No matching company or sector in this prototype.', 'No matching company or sector.')
path.write_text(inner, encoding='utf-8')
print('Converted sandboxed prototype wrapper into direct production dashboard')
