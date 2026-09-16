(()=>{
const pane=document.getElementById('p-weekly');if(!pane||document.getElementById('wkRevRun'))return;
const $=id=>document.getElementById(id),F=(v,d=1)=>Number.isFinite(Number(v))?Number(v).toFixed(d):'—';
const settings=pane.querySelector('.c:nth-of-type(2)');if(!settings)return;
const ac=settings.querySelector('.ac');
const b=document.createElement('button');b.id='wkRevRun';b.className='btn';b.textContent='Run Reverse-Only';
ac.insertBefore(b,$('wkName'));
const box=document.createElement('div');box.id='wkRevResults';box.className='c hide';box.style.marginTop='12px';box.innerHTML=`
<h2>Reverse-Only Weekly WMA–Gann</h2>
<div class=note>Primary setup is virtual only. Its P&amp;L is ignored. A trade is taken only after that virtual primary hits its selected SL on a completed 5-minute close. The currently selected reverse target and reverse SL settings are used. Position is carried positionally until target or SL. No repeated reversal.</div>
<div class=K style="margin-top:12px">
<div class=k><span>Reverse points</span><b id=wrNet>0</b></div>
<div class=k><span>Reverse trades</span><b id=wrTrades>0</b></div>
<div class=k><span>Win rate</span><b id=wrWin>0</b></div>
<div class=k><span>Max DD</span><b id=wrDD>0</b></div>
<div class=k><span>Targets</span><b id=wrTargets>0</b></div>
<div class=k><span>SL hits</span><b id=wrSL>0</b></div>
<div class=k><span>Long trades</span><b id=wrLong>0</b></div>
<div class=k><span>Short trades</span><b id=wrShort>0</b></div>
<div class=k><span>Long points</span><b id=wrLongPts>0</b></div>
<div class=k><span>Short points</span><b id=wrShortPts>0</b></div>
<div class=k><span>Positional carries</span><b id=wrCarry>0</b></div>
<div class=k><span>Virtual primary setups</span><b id=wrVirtual>0</b></div>
</div>
<div class=tw style="margin-top:12px"><table><thead><tr><th>Week</th><th>Entry date</th><th>Side</th><th>Entry</th><th>SL</th><th>Target</th><th>Exit date</th><th>Exit</th><th>Reason</th><th>Points</th></tr></thead><tbody id=wrRows></tbody></table></div>`;
pane.appendChild(box);
async function api(u,o){let r=await fetch(u,o),t=await r.text(),j;try{j=JSON.parse(t)}catch{throw Error(t||`HTTP ${r.status}`)}if(!r.ok)throw Error(j.detail||t);return j}
function body(){return {symbol:$('wkSym').value.trim()||'NIFTY 50',from_date:$('wkFrom').value,to_date:$('wkTo').value,wma_factor:+$('wkWma').value,gann_step:+$('wkStep').value,target_points:+$('wkTarget').value,reverse_target_points:+$('wkReverseTarget').value,gap_near_target_points:+$('wkGap').value,first_candle_distance_points:+$('wkFirstDistance').value,primary_stop_mode:$('wkPrimarySLMode').value,primary_stop_points:+$('wkPrimarySLPoints').value,reverse_stop_mode:$('wkReverseSLMode').value,reverse_stop_points:+$('wkReverseSLPoints').value,same_bar_policy:'stop_first'}}
function render(j){const s=j.summary||{};box.classList.remove('hide');$('wrNet').textContent=F(s.total_points);$('wrTrades').textContent=s.trades||0;$('wrWin').textContent=F(s.win_rate)+'%';$('wrDD').textContent=F(s.max_drawdown_points);$('wrTargets').textContent=s.targets||0;$('wrSL').textContent=s.stops||0;$('wrLong').textContent=s.long_trades||0;$('wrShort').textContent=s.short_trades||0;$('wrLongPts').textContent=F(s.long_points);$('wrShortPts').textContent=F(s.short_points);$('wrCarry').textContent=s.overnight_carries||0;$('wrVirtual').textContent=s.virtual_primary_setups||0;$('wrRows').innerHTML=(j.trades||[]).map(t=>`<tr><td>${t.week||''}</td><td>${t.entry_date||''}</td><td>${t.side||''}</td><td>${F(t.entry,2)}</td><td>${F(t.stop,2)}</td><td>${F(t.target,2)}</td><td>${t.exit_date||''}</td><td>${F(t.exit,2)}</td><td>${t.reason||''}</td><td class="${+t.points>=0?'pos':'neg'}">${F(t.points,2)}</td></tr>`).join('')||'<tr><td colspan=10>No reverse trades.</td></tr>'}
b.onclick=async()=>{b.disabled=true;const st=$('wkStatus');st.textContent='Running reverse-only positional backtest…';try{const j=await api('/api/weekly/reverse-only',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body())});render(j);st.textContent=`Reverse-only completed · ${F(j.summary.total_points)} points · ${j.summary.trades||0} trades`;}catch(e){st.textContent='Reverse-only error: '+e.message}finally{b.disabled=false}};
})();
