from html.parser import HTMLParser
from pathlib import Path
import re

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

p = SrcdocExtractor(); p.feed(text)
if not p.srcdoc:
    raise RuntimeError('Could not find embedded dashboard srcdoc')

inner = p.srcdoc
inner = inner.replace('Prototype mode<br>Free public/EOD data architecture<br>No trade recommendation', 'Production mode<br>Live Zerodha + backtest backend<br>Research only')
inner = inner.replace('No matching company or sector in this prototype.', 'No matching company or sector.')

# Remove every embedded/generated script, including the ChatGPT visualization host
# and the truncated prototype controller. The dashboard HTML/CSS itself is preserved.
inner = re.sub(r'<script\b[^>]*>.*?</script>', '', inner, flags=re.S | re.I)

controller = r'''<script>
(() => {
  const root = document.getElementById('mohit-investor-os');
  if (!root) return;
  const q = s => root.querySelector(s);
  const qa = s => Array.from(root.querySelectorAll(s));
  const id = x => q('#' + x);
  const labels = {overview:'Command deck',framework:'Master framework',orders:'Order intelligence',sectors:'Sector radar',volume:'High volume',h31:'H31 scanner',gann:'NIFTY Gann backtest',news:'Stock news'};

  function showPage(name) {
    qa('[data-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === name)));
    qa('[data-page]').forEach(p => {
      const on = p.dataset.page === name;
      p.classList.toggle('mio-hide', !on);
      p.style.display = on ? '' : 'none';
    });
    const t = id('mio-title'); if (t) t.textContent = labels[name] || name;
  }

  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-view]');
    if (b && root.contains(b)) { e.preventDefault(); e.stopPropagation(); showPage(b.dataset.view); }
  }, true);

  qa('[data-h31]').forEach(b => b.addEventListener('click', () => {
    qa('[data-h31]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    const selected = b.dataset.h31;
    qa('[data-status]').forEach(x => x.classList.toggle('mio-hide', selected !== 'all' && x.dataset.status !== selected));
  }));

  const search = id('mio-search'), noResults = id('mio-no-results');
  search?.addEventListener('input', () => {
    const query = search.value.trim().toLowerCase(); let visible = 0;
    qa('[data-company]').forEach(row => {
      const ok = !query || row.textContent.toLowerCase().includes(query);
      row.classList.toggle('mio-hide', !ok); if (ok) visible++;
    });
    noResults?.classList.toggle('mio-hide', !query || visible > 0);
  });

  const num = x => Number(id(x)?.value);
  const fmt = n => Number.isFinite(Number(n)) ? Number(n).toFixed(2) : '—';
  id('g-calc-wma')?.addEventListener('click', () => {
    const hi=num('g-prev-high'), lo=num('g-prev-low'), cl=num('g-prev-close'), f=num('g-factor');
    const out=id('g-wma-output');
    if (![hi,lo,cl,f].every(Number.isFinite) || hi < lo) { if(out) out.textContent='Enter valid previous-day High, Low and Close.'; return; }
    const w=(hi-lo)*f, r=cl+w, s=cl-w;
    if(out) out.innerHTML=`WMA <b>${fmt(w)}</b> · Resistance <b>${fmt(r)}</b> · Support <b>${fmt(s)}</b>`;
  });

  async function kiteStatus() {
    const el=id('g-kite-status');
    try {
      const r=await fetch('/api/kite/status',{cache:'no-store'}); const j=await r.json();
      if(el){ el.textContent=j.connected?`Connected · ${j.user_name||j.user_id||'Kite'}`:(j.configured?'Configured · login required':'Setup required'); el.style.color=j.connected?'var(--mio-good)':'var(--mio-gold)'; }
      return !!j.connected;
    } catch { if(el) el.textContent='API unavailable'; return false; }
  }

  id('g-kite-connect')?.addEventListener('click', async () => {
    const status=id('g-status');
    try {
      const r=await fetch('/api/kite/login-url',{cache:'no-store'}); const j=await r.json();
      if(!r.ok) throw new Error(j.detail || 'Kite API credentials are not configured.');
      location.href=j.url;
    } catch(e) { if(status) status.textContent='Error: '+e.message; }
  });

  function setText(name,val){ const el=id(name); if(el) el.textContent=val; }
  function render(j){
    const s=j.summary||{}, trades=j.trades||[];
    setText('g-k-trades', String(s.trades ?? trades.length));
    setText('g-k-win', `${Number(s.win_rate||0).toFixed(1)}%`);
    setText('g-k-points', Number(s.total_points||0).toFixed(1));
    setText('g-k-dd', Number(s.max_drawdown||0).toFixed(1));
    setText('g-k-targets', String(s.targets ?? trades.filter(x=>x.reason==='TARGET').length));
    setText('g-k-sl', String(s.sl_hits ?? trades.filter(x=>x.reason==='SL').length));
    setText('g-k-eod', String(s.eod_exits ?? trades.filter(x=>x.reason==='EOD').length));
    setText('g-k-days', String(s.test_days ?? s.days ?? '—'));
    const ledger=id('g-ledger');
    if(ledger){
      ledger.innerHTML = trades.length ? trades.map(t => `<tr><td>${t.date||''}</td><td>${t.trade_no??''}</td><td>${t.touch||''}</td><td>${fmt(t.buy)}</td><td>${fmt(t.sell)}</td><td>${t.side||''}</td><td>${t.entry_time||t.entryTime||''}</td><td>${fmt(t.entry)}</td><td>${t.exit_time||t.exitTime||''}</td><td>${fmt(t.exit)}</td><td>${t.reason||''}</td><td class="${Number(t.points)>=0?'mio-positive':'mio-negative'}">${Number(t.points)>=0?'+':''}${fmt(t.points)}</td></tr>`).join('') : '<tr><td colspan="12" class="mio-empty">No trades returned.</td></tr>';
    }
  }

  id('g-run')?.addEventListener('click', async () => {
    const status=id('g-status');
    try {
      const from=id('g-from')?.value, to=id('g-to')?.value;
      if(!from || !to) throw new Error('Choose From and To dates first.');
      if(status) status.textContent='Fetching Zerodha 1-minute candles and running backtest…';
      const raw=id('g-maxtr')?.value?.trim();
      const body={from_date:from,to_date:to,wma_factor:0.382,target_points:Number(id('g-target')?.value||100),gann_step:0.125,reentry:true,max_trades_per_day:raw?Number(raw):null,same_bar_policy:'stop_first'};
      const r=await fetch('/api/backtest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const j=await r.json(); if(!r.ok) throw new Error(j.detail||'Backtest failed');
      render(j); if(status) status.textContent=`Completed · ${j.candles||0} one-minute candles`;
    } catch(e) { if(status) status.textContent='Error: '+e.message; }
  });

  id('g-clear')?.addEventListener('click', () => {
    ['g-k-trades','g-k-points','g-k-targets','g-k-sl','g-k-eod','g-k-days'].forEach(x=>setText(x,'0'));
    setText('g-k-win','—'); setText('g-k-dd','—');
    const ledger=id('g-ledger'); if(ledger) ledger.innerHTML='<tr><td colspan="12" class="mio-empty">No backtest run yet.</td></tr>';
    setText('g-status','Ready. 1-minute close confirmation + same-day re-entry are locked on.');
  });

  showPage(new URLSearchParams(location.search).get('connected')==='1' ? 'gann' : 'overview');
  kiteStatus();
})();
</script>'''

if '</body>' in inner:
    inner = inner.replace('</body>', controller + '\n</body>')
else:
    inner += controller

path.write_text(inner, encoding='utf-8')
print('Direct production dashboard created; all generated scripts replaced')
