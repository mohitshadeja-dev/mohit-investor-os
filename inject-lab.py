from pathlib import Path
p=Path('/app/app/static/index.html')
s=p.read_text(encoding='utf-8')
for tag in [
    '<script src="/static/lab-upgrade.js"></script>',
    '<script src="/static/presets-7750.js"></script>',
    '<script src="/static/weekly-wma-gann.js"></script>',
    '<script src="/static/stockmock-worklist.js"></script>',
    '<script src="/static/live-trading-window.js"></script>'
    ,'<script src="/static/journal-diary.js"></script>'
]:
    if tag not in s:
        s=s.replace('</body>',tag+'\n</body>')
p.write_text(s,encoding='utf-8')
