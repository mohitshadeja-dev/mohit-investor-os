(()=>{
const q=id=>document.getElementById(id);
const g=q('gann'); if(!g) return;
const wrap=document.createElement('div');
wrap.innerHTML=`
<div class="grid grid2" style="margin-top:16px">
  <div class="panel">
    <h2>4. Live Gann alerts</h2>
    <div class="muted">Auto-checks Zerodha once per minute. Entry only after a 1-minute candle CLOSE crosses the Gann trigger. Same-day re-entry stays enabled.</div>
    <div class="kpis" style="margin-top:14px">
      <div class="kpi"><span>Status</span><b id="liveState">—</b></div>
      <div class="kpi"><span>Buy above</span><b id="liveBuy">—</b></div>
      <div class="kpi"><span>Sell below</span><b id="liveSell">—</b></div>
      <div class="kpi"><span>Latest NIFTY</span><b id="liveClose">—</b></div>
    </div>
    <div class="actions"><button class="btn good" id="startLive">Start live alerts</button><button class="btn" id="stopLive">Stop</button><button class="btn" id="notifyLive">Enable browser alert</button><span class="status" id="liveMsg">Stopped.</span></div>
    <div id="openTrade" class="panel" style="margin-top:12px;padding:12px;display:none"></div>
  </div>
  <div class="panel">
    <h2>5. Trading journal</h2>
    <div class="kpis">
      <div class="kpi"><span>Journal trades</span><b id="jTrades">0</b></div>
      <div class="kpi"><span>Win rate</span><b id="jWin">—</b></div>
      <div class="kpi"><span>Cumulative points</span><b id="jPoints">0.0</b></div>
      <div class="kpi"><span>Closed</span><b id="jClosed">0</b></div>
    </div>
    <div class="muted" style="margin-top:10px">Trades detected by the live Gann engine are journaled automatically.</div>
  </div>
</div>
<div class="panel" style="margin-top:16px">
  <h2>Live journal ledger</h2>
  <div class="tablewrap"><table><thead><tr><th>Date</th><th>#</th><th>Side</th><th>Entry time</th><th>Entry</th><th>SL</th><th>Target</th><th>Exit time</th><th>Exit</th><th>Reason</th><th>Points</th><th>Status</th></tr></thead><tbody id="journalBody"><tr><td colspan="12" class="muted">No journal trades yet.</td></tr></tbody></table></div>
</div>`;
g.appendChild(wrap);
const $=id=>document.getElementById(id), fmt=n=>n===null||n===undefined?'—':Number(n).toFixed(2);
let timer=null,lastAlertKey='';
async function asJson(r){const txt=await r.text(); try{return JSON.parse(txt)}catch{return {detail:txt||`HTTP ${r.status}`}}}
async function loadJournal(){
 try{const r=await fetch('/api/journal',{cache:'no-store'}),j=await asJson(r); if(!r.ok)throw Error(j.detail||'Journal failed');
 const s=j.summary||{},t=j.trades||[]; $('jTrades').textContent=s.trades??0;$('jClosed').textContent=s.closed??0;$('jWin').textContent=Number(s.win_rate||0).toFixed(1)+'%';$('jPoints').textContent=Number(s.total_points||0).toFixed(1);
 $('journalBody').innerHTML=t.length?t.map(x=>`<tr><td>${x.trade_date||''}</td><td>${x.trade_no||''}</td><td>${x.side||''}</td><td>${x.entry_time||''}</td><td>${fmt(x.entry)}</td><td>${fmt(x.stop)}</td><td>${fmt(x.target)}</td><td>${x.exit_time||''}</td><td>${fmt(x.exit)}</td><td>${x.reason||''}</td><td class="${Number(x.points)>=0?'good':'bad'}">${x.points==null?'—':(Number(x.points)>=0?'+':'')+fmt(x.points)}</td><td>${x.status||''}</td></tr>`).join(''):'<tr><td colspan="12" class="muted">No journal trades yet.</td></tr>';
 }catch(e){$('journalBody').innerHTML=`<tr><td colspan="12" class="bad">${e.message}</td></tr>`}
}
function browserAlert(title,body){if(Notification?.permission==='granted')new Notification(title,{body});}
async function pollLive(){
 try{
  const target=Number(q('target')?.value||100); const r=await fetch('/api/live/gann?target_points='+encodeURIComponent(target),{cache:'no-store'}),j=await asJson(r); if(!r.ok)throw Error(j.detail||'Live check failed');
  $('liveState').textContent=j.state||'—';$('liveBuy').textContent=fmt(j.buy_above);$('liveSell').textContent=fmt(j.sell_below);$('liveClose').textContent=fmt(j.latest_close);$('liveMsg').textContent=j.message||'';
  const tr=j.current_trade, box=$('openTrade');
  if(tr){box.style.display='block';box.innerHTML=`<b>${tr.side} ${tr.status}</b><div class="muted" style="margin-top:5px">Entry ${fmt(tr.entry)} · SL ${fmt(tr.stop)} · Target ${fmt(tr.target)} · ${tr.reason||'running'}</div>`;
   const key=[tr.date,tr.trade_no,tr.status,tr.reason||''].join('|'); if(key!==lastAlertKey){lastAlertKey=key; browserAlert(`NIFTY Gann ${tr.side}`,tr.status==='OPEN'?`Entry ${fmt(tr.entry)} | SL ${fmt(tr.stop)} | Target ${fmt(tr.target)}`:`${tr.reason} | ${fmt(tr.points)} points`);}
  } else box.style.display='none';
  await loadJournal();
 }catch(e){$('liveMsg').textContent='Error: '+e.message}
}
$('startLive').onclick=()=>{if(timer)clearInterval(timer);pollLive();timer=setInterval(pollLive,60000);$('liveMsg').textContent='Live alerts started.'};
$('stopLive').onclick=()=>{if(timer)clearInterval(timer);timer=null;$('liveMsg').textContent='Stopped.'};
$('notifyLive').onclick=async()=>{if(!('Notification'in window)){ $('liveMsg').textContent='Browser notifications not supported.';return;}const p=await Notification.requestPermission();$('liveMsg').textContent=p==='granted'?'Browser alerts enabled.':'Browser alerts not enabled.'};
loadJournal();
})();