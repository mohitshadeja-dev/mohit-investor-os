from pathlib import Path
p=Path('/app/app/static/index.html')
s=p.read_text(encoding='utf-8')
tag='<script src="/static/live-journal.js"></script>'
if tag not in s:
    s=s.replace('</body>',tag+'</body>')
p.write_text(s,encoding='utf-8')
print('Injected live Gann alerts and journal UI')
