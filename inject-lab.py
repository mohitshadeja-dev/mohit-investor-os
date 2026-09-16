from pathlib import Path
p=Path('/app/app/static/index.html')
s=p.read_text(encoding='utf-8')
tag='<script src="/static/lab-upgrade.js"></script>'
if tag not in s:
    s=s.replace('</body>',tag+'\n</body>')
p.write_text(s,encoding='utf-8')
