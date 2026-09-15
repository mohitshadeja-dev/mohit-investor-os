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
inner = inner.replace('Prototype mode<br>Free public/EOD data architecture<br>No trade recommendation', 'Production mode<br>Zerodha-connected backtest<br>Research only')
inner = inner.replace('No matching company or sector in this prototype.', 'No matching company or sector.')

# The generated prototype contains a truncated final inline script. Remove that script entirely
# and replace it with a clean production controller.
inner = re.sub(r'<script>\s*\(\(\)\s*=>\s*\{\s*const root = document\.getElementById\(\'mohit-investor-os\'\);.*?</script>', '', inner, flags=re.S)

controller = r'''<script>
(() => {
  const root = document.getElementById('mohit-investor-os');
  if (!root) return;
  const q = s => root.querySelector(s);
  const qa = s => [...root.querySelectorAll(s)];
  const byId = id => q('#' + id);
  const num = id => Number(byId(id)?.value);
  const fmt = n => Number.isFinite(Number(n)) ? Number(n).toFixed(2) : '—';

  const labels = {overview:'Command deck',framework:'Master framework',orders:'Order intelligence',sectors:'Sector radar',volume:'High volume',h31:'H31 scanner',gann:'NIFTY Gann backtest',news:'Stock news'};
  function showPage(name){
    qa('[data-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === name)));
    qa('[data-page]').forEach(p => p.classList.toggle('mio-hide', p.dataset.page !== name));
    const title = byId('mio-title'); if (title) title.textContent = labels[name] || name;
  }
  qa('[data-view]').forEach(b => b.addEventListener('click', e => { e.preventDefault(); showPage(b.dataset.view); }));

  qa('[data-h31]').forEach(b => b.addEventListener('click', () => {
    qa('[data-h31]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    const selected=b.dataset.h31;
    qa('[data-status]').forEach(x => x.classList.toggle('mio-hide', selected !== 'all' && x.dataset.status !== selected));
  }));

  const search = byId('mio-search'), noResults = byId('mio-no-results');
  if (search) search.addEventListener('input', () => {
    const query=search.value.trim().toLowerCase(); let visible=0;
    qa('[data-company]').forEach(row => { const ok=!query || row.textContent.toLowerCase().includes(query); row.classList.toggle('mio-hide',!ok); if(ok) visible++; });
    if(noResults) noResults.classList.toggle('mio-hide', !query || visible>0);
  });

  byId('g-calc-wma')?.addEventListener('click', () => {
    const hi=num('g-prev-high'), lo=num('g-prev-low'), cl=num('g-prev-close'), f=num('g-factor');
    if (![hi,lo,cl,f].every(Number.isFinite) || hi < lo) { byId('g-wma-output').textContent='Enter valid previous-day High, Low and Close.'; return; }
    const w=(hi-lo)*f, r=cl+w, s=cl-w;
    byId('g-wma-output').innerHTML=`WMA <b>${fmt(w)}</b> · Resistance <b>${fmt(r)}</b> · Support <b>${fmt(s)}</b>`;
  });

  async function kiteStatus(){
    const el=byId('g-kite-status');
    try{
      const r=await fetch('/api/kite/status',{cache:'no-store'}); const j=await r.json();
      if(el){ el.textContent=j.connected?`Connected · ${j.user_name||j.user_id||'Kite'}`:(j.configured?'Configured · login required':'Setup required'); el.style.color=j.connected?'var(--mio-good)':'var(--mio-gold)'; }
      return !!j.connected;
    }catch(e){ if(el) el.textContent='API unavailable'; return false; }
  }

  byId('g-kite-connect')?.addEventListener('click', async () => {
    try{
      const r=await fetch('/api/kite/login-url',{cache:'no-store'}), j=await r.json();
      if(!r.ok) throw new Error(j.detail || 'Kite API credentials are not configured.');
      window.location.assign(j.url);
    }catch(e){ byId('g-status').textContent=e.message; }
  });

  function renderResult(j){
    const s=j.summary||{}, trades=j.trades||[];
    byId('g-k-trades').textContent=s.trades ?? trades.length;
    byId('g-k-win').textContent=(s.win_rate ?? 0).toFixed ? `${Number(s.win_rate).toFixed(1)}%` : `${s.win_rate||0}%`;
    byId('g-k-points').textContent=Number(s.total_points||0).toFixed(1);
    byId('g-k-dd').textContent=Number(s.max_drawdown||0).toFixed(1);
    byId('g-k-targets').textContent=s.targets ?? trades.filter(x=>x.reason==='TARGET').length;
    byId('g-k-sl').textContent=s.sl_hits ?? trades.filter(x=>x.reason==='SL').length;
    byId('g-k-eod').textContent=s.eod_exits ?? trades.filter(x=>x.reason==='EOD').length;
    byId('g-k-days').textContent=s.test_days ?? s.days ?? '—';
    const ledger=byId('g-ledger');
    if(ledger){
      ledger.innerHTML = trades.length ? trades.map(t => `<tr><td>${t.date||''}</td><td>${t.trade_no??''}</td><td>${t.side||''}</td><td>${t.entry_time||t.entryTime||''}</td><td>${fmt(t.entry)}</td><td>${t.exit_time||t.exitTime||''}</td><td>${fmt(t.exit)}</td><td>${t.reason||''}</td><td class="${Number(t.points)>=0?'mio-positive':'mio-negative'}">${Number(t.points)>=0?'+':''}${fmt(t.points)}</td></tr>`).join('') : '<tr><td colspan="9">No trades returned.</td></tr>';
    }
  }

  byId('g-run')?.addEventListener('click', async () => {
    const status=byId('g-status');
    try{
      const from=byId('g-from')?.value, to=byId('g-to')?.value;
      if(!from || !to) throw new Error('Choose From and To dates first.');
      status.textContent='Fetching Zerodha 1-minute data and running backtest…';
      const maxRaw=byId('g-maxtr')?.value?.trim();
      const body={from_date:from,to_date:to,wma_factor:0.382,target_points:Number(byId('g-target')?.value||100),gann_step:0.125,reentry:true,max_trades_per_day:maxRaw?Number(maxRaw):null,same_bar_policy:'stop_first'};
      const r=await fetch('/api/backtest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const j=await r.json(); if(!r.ok) throw new Error(j.detail||'Backtest failed');
      renderResult(j); status.textContent=`Completed · ${j.candles?.toLocaleString?.()||j.candles||0} one-minute candles`;
    }catch(e){ status.textContent='Error: '+e.message; }
  });

  byId('g-clear')?.addEventListener('click', () => {
    ['g-k-trades','g-k-points','g-k-targets','g-k-sl','g-k-eod','g-k-days'].forEach(id=>{if(byId(id)) byId(id).textContent='0';});
    if(byId('g-k-win')) byId('g-k-win').textContent='—'; if(byId('g-k-dd')) byId('g-k-dd').textContent='—';
    if(byId('g-ledger')) byId('g-ledger').innerHTML=''; if(byId('g-status')) byId('g-status').textContent='Ready. 1-minute close confirmation + same-day re-entry are locked on.';
  });

  showPage('overview');
  kiteStatus();
  if(new URLSearchParams(location.search).get('connected')==='1'){ showPage('gann'); if(byId('g-status')) byId('g-status').textContent='Zerodha login completed.'; }
})();
</script>'''
inner = inner.replace('</body>', controller + '\n</body>')
path.write_text(inner, encoding='utf-8')
print('Converted wrapper and injected working production controller')
