from pathlib import Path

p = Path('/app/app/static/index.html')
s = p.read_text(encoding='utf-8')
tag = '<script src="/static/realtime-alerts.js"></script>'
if tag not in s:
    s = s.replace('</body>', tag + '</body>')
p.write_text(s, encoding='utf-8')
print('Injected NIFTY 39 and NIFTY100 Stock A+ real-time alert centre')

p = Path('/app/app/main.py')
s = p.read_text(encoding='utf-8')
import_line = 'from .realtime_alerts import router as realtime_alerts_router\n'
include_line = 'app.include_router(realtime_alerts_router)\n'
if import_line not in s:
    marker = 'from .sandbox_7576 import run_7576_sandbox\n'
    s = s.replace(marker, marker + import_line)
if include_line not in s:
    marker = "app=FastAPI(title='Mohit Investor OS — Strategy Lab',version='3.0.0')\n"
    s = s.replace(marker, marker + include_line)
p.write_text(s, encoding='utf-8')
print('Registered real-time alert API routes')
