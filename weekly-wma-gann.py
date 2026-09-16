from __future__ import annotations
from datetime import time
from math import floor, sqrt
import pandas as pd

IST='Asia/Kolkata'

def _norm(df):
    d=df.copy(); d.columns=[str(c).lower() for c in d.columns]
    d['date']=pd.to_datetime(d['date'],errors='coerce')
    if d['date'].dt.tz is None: d['date']=d['date'].dt.tz_localize(IST)
    else: d['date']=d['date'].dt.tz_convert(IST)
    for c in ('open','high','low','close'): d[c]=pd.to_numeric(d[c],errors='coerce')
    d=d.dropna(subset=['date','open','high','low','close']).sort_values('date')
    lt=d.date.dt.time
    return d[(lt>=time(9,15))&(lt<=time(15,30))].reset_index(drop=True)

def _gann(price,step=.125):
    r=sqrt(float(price)); n=floor((r+1e-10)/step)
    return round(((n+1)*step)**2,2), round((n*step)**2,2)

def _fib_levels(low,high):
    rng=float(high)-float(low)
    return float(low)+.382*rng, float(low)+.618*rng

def _bars5(day):
    x=day.set_index('date')
    return x.resample('5min',origin='start_day',offset='15min').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last')).dropna().reset_index()

def _first_close_break(day,resistance,support):
    for _,b in _bars5(day).iterrows():
        c=float(b.close)
        if c>=resistance:return {'type':'RESISTANCE','level':float(resistance),'time':b.date,'close':c}
        if c<=support:return {'type':'SUPPORT','level':float(support),'time':b.date,'close':c}
    return None

def _run_leg_same_day(side,entry,stop,target,day,start_time,leg):
    bars=_bars5(day); bars=bars[bars.date>start_time].reset_index(drop=True)
    if len(bars):
        last=bars.iloc[-1]; ex=float(last.close); ext=last.date
    else:
        ex=float(entry); ext=start_time
    reason='DAY_EOD'
    for _,bar in bars.iterrows():
        c=float(bar.close)
        hit_sl=c<=stop if side=='LONG' else c>=stop
        hit_t=c>=target if side=='LONG' else c<=target
        if hit_sl:
            ex=c; ext=bar.date; reason='SL'; break
        if hit_t:
            ex=c; ext=bar.date; reason='TARGET'; break
    pts=(ex-entry) if side=='LONG' else (entry-ex)
    return {'leg':leg,'side':side,'entry':float(entry),'stop':float(stop),'target':float(target),'exit':float(ex),'exit_time':ext,'reason':reason,'points':float(pts)}

def _summary(trades,weeks,skipped_gap,no_setup,blocked_tuesday,blocked_fib):
    pts=[float(t['points']) for t in trades]; wins=[p for p in pts if p>0]; losses=[p for p in pts if p<0]
    eq=peak=dd=0
    for p in pts:
        eq+=p; peak=max(peak,eq); dd=max(dd,peak-eq)
    primary=[t for t in trades if t.get('leg','PRIMARY')=='PRIMARY']; reverse=[t for t in trades if t.get('leg')=='REVERSE']
    return {
        'weeks':weeks,'traded_weeks':len({t['week'] for t in primary}),'trades':len(trades),'primary_trades':len(primary),'reverse_trades':len(reverse),
        'wins':len(wins),'losses':len(losses),'win_rate':round(100*len(wins)/len(trades),2) if trades else 0,
        'total_points':round(sum(pts),2),'avg_points':round(sum(pts)/len(trades),2) if trades else 0,
        'targets':sum(t['reason']=='TARGET' for t in trades),'stops':sum(t['reason']=='SL' for t in trades),'day_eod_exits':sum(t['reason']=='DAY_EOD' for t in trades),
        'max_drawdown_points':round(dd,2),'skipped_gap_days':skipped_gap,'no_setup_days':no_setup,
        'blocked_by_tuesday_range':blocked_tuesday,'blocked_by_fibonacci':blocked_fib,
        'wednesday_trades':sum(t['trade_day']=='WED' and t.get('leg','PRIMARY')=='PRIMARY' for t in trades),
        'thursday_trades':sum(t['trade_day']=='THU' and t.get('leg','PRIMARY')=='PRIMARY' for t in trades),
        'friday_trades':sum(t['trade_day']=='FRI' and t.get('leg','PRIMARY')=='PRIMARY' for t in trades),
        'long_points':round(sum(t['points'] for t in trades if t['side']=='LONG'),2),'short_points':round(sum(t['points'] for t in trades if t['side']=='SHORT'),2),
        'primary_points':round(sum(t['points'] for t in primary),2),'reverse_points':round(sum(t['points'] for t in reverse),2),
        'overnight_carries':0,
    }

def run_weekly(df,wma_factor=.382,gann_step=.125,target_points=100.0,gap_near_target_points=30.0,same_bar_policy='stop_first'):
    """Weekly WMA-Gann. All signals/exits use completed 5-minute closes. No overnight carry.

    Wed uses Tue H/L/C. If no valid primary trade Wed, Thu recalculates from Wed; if still none, Fri recalculates from Thu.
    BUY requires 5m close >= Gann Buy, > Tuesday High, and > previous-day Fib 0.382.
    SELL requires 5m close <= Gann Sell, < Tuesday Low, and < previous-day Fib 0.618.
    Primary target = 100 points. Opposite Gann boundary is SL, both on 5m closing basis.
    If primary SL occurs, take one reverse trade at that same confirming 5m close, target 100, opposite Gann SL.
    Primary and reverse are intraday only. If target/SL is not hit, exit at that day's final 5m close.
    Maximum one primary setup per week and one reverse leg only.
    """
    d=_norm(df); d['session']=d.date.dt.date
    sessions={k:v.drop(columns='session').reset_index(drop=True) for k,v in d.groupby('session',sort=True)}
    dates=sorted(sessions); groups={}
    for sd in dates:
        iso=pd.Timestamp(sd).isocalendar(); groups.setdefault((int(iso.year),int(iso.week)),[]).append(sd)
    trades=[]; attempts=[]; skipped_gap=no_setup=blocked_tuesday=blocked_fib=0; week_count=0
    for wk,wdates in sorted(groups.items()):
        week_count+=1; bywd={pd.Timestamp(x).weekday():x for x in wdates}
        anchor_candidates=[x for x in wdates if pd.Timestamp(x).weekday()<=1]
        if not anchor_candidates:continue
        anchor=max(anchor_candidates); anchor_day=sessions[anchor]
        tuesday_high=float(anchor_day.high.max()); tuesday_low=float(anchor_day.low.min()); primary_done=False
        for wd,label in ((2,'WED'),(3,'THU'),(4,'FRI')):
            if primary_done:break
            cand=bywd.get(wd)
            if cand is None:continue
            prevs=[x for x in dates if x<cand]
            if not prevs:continue
            ref=max(prevs); prev=sessions[ref]; day=sessions[cand]
            ph=float(prev.high.max()); pl=float(prev.low.min()); pc=float(prev.iloc[-1].close)
            fib382,fib618=_fib_levels(pl,ph); wma=(ph-pl)*float(wma_factor); resistance=pc+wma; support=pc-wma
            touch=_first_close_break(day,resistance,support)
            rec={'week':f'{wk[0]}-W{wk[1]:02d}','trade_day':label,'date':str(cand),'reference_date':str(ref),'tuesday_date':str(anchor),
                 'tuesday_high':round(tuesday_high,2),'tuesday_low':round(tuesday_low,2),'ref_high':round(ph,2),'ref_low':round(pl,2),
                 'fib_382':round(fib382,2),'fib_618':round(fib618,2),'support':round(support,2),'resistance':round(resistance,2)}
            if touch is None:
                no_setup+=1; rec['status']='NO_5M_CLOSE_WMA_BREAK'; attempts.append(rec); continue
            buy,sell=_gann(touch['level'],float(gann_step)); rec.update({'first_touch':touch['type'],'touch_close':round(touch['close'],2),'buy_above':buy,'sell_below':sell})
            b5=_bars5(day); b5=b5[b5.date>=touch['time']].reset_index(drop=True)
            travel=max(0.0,float(target_points)-float(gap_near_target_points)); first_close=float(b5.iloc[0].close) if len(b5) else None
            if first_close is not None and (first_close>=buy+travel or first_close<=sell-travel):
                skipped_gap+=1; rec.update({'status':'GAP_NEAR_TARGET_SKIP','filter_close':round(first_close,2)}); attempts.append(rec); continue
            entrybar=None; side=None; saw_gann=False; saw_tuesday_ok=False
            for _,b in b5.iterrows():
                c=float(b.close); lg=c>=buy; sg=c<=sell
                long_tue=c>tuesday_high; short_tue=c<tuesday_low; long_fib=c>fib382; short_fib=c<fib618
                if lg and long_tue and long_fib:entrybar=b; side='LONG'; break
                if sg and short_tue and short_fib:entrybar=b; side='SHORT'; break
                if lg or sg:saw_gann=True
                if (lg and long_tue) or (sg and short_tue):saw_tuesday_ok=True
            if side is None:
                if saw_tuesday_ok:blocked_fib+=1; rec['status']='BLOCKED_BY_PREVIOUS_DAY_FIB'
                elif saw_gann:blocked_tuesday+=1; rec['status']='BLOCKED_BY_TUESDAY_HIGH_LOW'
                else:no_setup+=1; rec['status']='NO_5M_CLOSE_GANN_TRIGGER'
                attempts.append(rec); continue

            entry=float(entrybar.close); stop=float(sell if side=='LONG' else buy); target=float(entry+target_points if side=='LONG' else entry-target_points)
            leg1=_run_leg_same_day(side,entry,stop,target,day,entrybar.date,'PRIMARY')
            t1={**rec,'status':'TRADE','leg':'PRIMARY','side':side,'touch_time':str(touch['time']),'entry_date':str(cand),'entry_time':str(entrybar.date),
                'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),'exit_date':str(cand),'exit_time':str(leg1['exit_time']),
                'exit':round(leg1['exit'],2),'reason':leg1['reason'],'points':round(leg1['points'],2),'carried_overnight':False}
            trades.append(t1); attempts.append(t1); primary_done=True

            if leg1['reason']=='SL':
                rev_side='SHORT' if side=='LONG' else 'LONG'; rev_entry=float(leg1['exit'])
                rev_stop=float(buy if rev_side=='SHORT' else sell); rev_target=float(rev_entry-100 if rev_side=='SHORT' else rev_entry+100)
                leg2=_run_leg_same_day(rev_side,rev_entry,rev_stop,rev_target,day,leg1['exit_time'],'REVERSE')
                t2={**rec,'status':'REVERSE_TRADE','leg':'REVERSE','side':rev_side,'touch_time':str(touch['time']),'entry_date':str(cand),
                    'entry_time':str(leg1['exit_time']),'entry':round(rev_entry,2),'stop':round(rev_stop,2),'target':round(rev_target,2),
                    'exit_date':str(cand),'exit_time':str(leg2['exit_time']),'exit':round(leg2['exit'],2),'reason':leg2['reason'],
                    'points':round(leg2['points'],2),'carried_overnight':False,'reversed_from':side}
                trades.append(t2); attempts.append(t2)
    return _summary(trades,week_count,skipped_gap,no_setup,blocked_tuesday,blocked_fib),trades,attempts
