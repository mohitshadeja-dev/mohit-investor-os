from pathlib import Path
import html
import re

p = Path('/app/app/static/index.html')
s = p.read_text(encoding='utf-8')

m = re.search(r'data-srcdoc="(.*?)"\s*>', s, flags=re.S)
if not m:
    raise SystemExit('Could not find data-srcdoc wrapper in app/static/index.html')

inner = html.unescape(m.group(1))
inner = inner.replace('Prototype mode', 'Production mode')
inner = inner.replace('Free public/EOD data architecture', 'Live Zerodha + backtest backend')

# Keep the production dashboard as the top-level page so navigation, fetch(),
# redirects and Zerodha callbacks operate on the same origin.
p.write_text(inner, encoding='utf-8')
print('Prepared production frontend:', p)
