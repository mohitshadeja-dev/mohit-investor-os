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

def _bars5(day):
    x=day.set_index('date')
    return x.resample('5min',origin='start_day',offset='15min').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last')).dropna().reset_index()

def _first_close_break(day,resistance,support):
    """First WMA level confirmation using 5-minute CLOSE only."""
    for _,b in _bars5(day).iterrows():
        c=float(b.close)
        if c>=resistance:
            return {'type':'RESISTANCE','level':float(resistance),'time':b.date,'close':c}
        if c<=support:
            return {'type':'SUPPORT','level':float(support),'time':b.date,'close':c}
    return None

def _summary(trades,weeks,skipped_gap,no_setup,blocked_tuesday):
    pts=[float(t['points']) for t in trades]; wins=[p for p in pts if p>0]; losses=[p for p in pts if p<0]
    eq=peak=dd=0
    for p in pts:
        eq+=p; peak=max(peak,eq); dd=max(dd,peak-eq)
    return {
        'weeks':weeks,'traded_weeks':len(trades),'trades':len(trades),'wins':len(wins),'losses':len(losses),
        'win_rate':round(100*len(wins)/len(trades),2) if trades else 0,
        'total_points':round(sum(pts),2),'avg_points':round(sum(pts)/len(trades),2) if trades else 0,
        'targets':sum(t['reason']=='TARGET' for t in trades),'stops':sum(t['reason']=='SL' for t in trades),
        'eod':sum(t['reason']=='EOD' for t in trades),'max_drawdown_points':round(dd,2),
        'skipped_gap_days':skipped_gap,'no_setup_days':no_setup,'ambiguous_touch_days':0,
        'blocked_by_tuesday_range':blocked_tuesday,
        'wednesday_trades':sum(t['trade_day']=='WED' for t in trades),'thursday_trades':sum(t['trade_day']=='THU' for t in trades),'friday_trades':sum(t['trade_day']=='FRI' for t in trades),
        'long_points':round(sum(t['points'] for t in trades if t['side']=='LONG'),2),
        'short_points':round(sum(t['points'] for t in trades if t['side']=='SHORT'),2),
    }

def run_weekly(df,wma_factor=.382,gann_step=.125,target_points=100.0,gap_near_target_points=30.0,same_bar_policy='stop_first'):
    """Weekly WMA-Gann strategy — ALL trading decisions use completed 5-minute candle closes.

    Tuesday expiry anchor -> Wednesday attempt using Tuesday H/L/C.
    If no valid trade Wednesday: recalculate from Wednesday -> Thursday.
    If no valid trade Thursday: recalculate from Thursday -> Friday.

    Rules:
      1) WMA Support/Resistance is considered reached only when a 5m candle CLOSES beyond it.
      2) Gann levels are calculated from that confirmed WMA boundary.
      3) LONG only on a 5m CLOSE >= Gann Buy Above AND > Tuesday High.
      4) SHORT only on a 5m CLOSE <= Gann Sell Below AND < Tuesday Low.
      5) Target is reached only on a 5m CLOSE at/through entry +/- target points.
      6) SL is hit only on a 5m CLOSE beyond the opposite Gann boundary.
      7) EOD exit = final completed 5m close.
      8) Maximum ONE trade for the whole week.
      9) Near-target skip is also evaluated from a completed 5m close, not intrabar/open.
    """
    d=_norm(df); d['session']=d.date.dt.date
    sessions={k:v.drop(columns='session').reset_index(drop=True) for k,v in d.groupby('session',sort=True)}
    dates=sorted(sessions)
    groups={}
    for sd in dates:
        iso=pd.Timestamp(sd).isocalendar(); groups.setdefault((int(iso.year),int(iso.week)),[]).append(sd)
    trades=[]; attempts=[]; skipped_gap=no_setup=blocked_tuesday=0; week_count=0
    for wk,wdates in sorted(groups.items()):
        week_count+=1
        bywd={pd.Timestamp(x).weekday():x for x in wdates}
        anchor_candidates=[x for x in wdates if pd.Timestamp(x).weekday()<=1]
        if not anchor_candidates: continue
        anchor=max(anchor_candidates)
        anchor_day=sessions[anchor]
        tuesday_high=float(anchor_day.high.max()); tuesday_low=float(anchor_day.low.min())
        traded=False
        for wd,label in ((2,'WED'),(3,'THU'),(4,'FRI')):
            if traded: break
            cand=bywd.get(wd)
            if cand is None: continue
            prevs=[x for x in dates if x<cand]
            if not prevs: continue
            ref=max(prevs); prev=sessions[ref]; day=sessions[cand]
            ph=float(prev.high.max()); pl=float(prev.low.min()); pc=float(prev.iloc[-1].close)
            wma=(ph-pl)*float(wma_factor); resistance=pc+wma; support=pc-wma
            touch=_first_close_break(day,resistance,support)
            rec={'week':f'{wk[0]}-W{wk[1]:02d}','trade_day':label,'date':str(cand),'reference_date':str(ref),
                 'tuesday_date':str(anchor),'tuesday_high':round(tuesday_high,2),'tuesday_low':round(tuesday_low,2),
                 'support':round(support,2),'resistance':round(resistance,2)}
            if touch is None:
                no_setup+=1; rec['status']='NO_5M_CLOSE_WMA_BREAK'; attempts.append(rec); continue
            buy,sell=_gann(touch['level'],float(gann_step)); rec.update({'first_touch':touch['type'],'touch_close':round(touch['close'],2),'buy_above':buy,'sell_below':sell})
            b5=_bars5(day); b5=b5[b5.date>=touch['time']].reset_index(drop=True)
            # 20-30 point near-target skip is checked only from completed 5m closes.
            travel=max(0.0,float(target_points)-float(gap_near_target_points))
            first_close=float(b5.iloc[0].close) if len(b5) else None
            if first_close is not None and (first_close>=buy+travel or first_close<=sell-travel):
                skipped_gap+=1; rec.update({'status':'GAP_NEAR_TARGET_SKIP','filter_close':round(first_close,2)}); attempts.append(rec); continue
            entrybar=None; side=None; saw_gann_without_tuesday=False
            for _,b in b5.iterrows():
                c=float(b.close)
                long_gann=c>=buy; short_gann=c<=sell
                if long_gann and c>tuesday_high:
                    entrybar=b; side='LONG'; break
                if short_gann and c<tuesday_low:
                    entrybar=b; side='SHORT'; break
                if long_gann or short_gann: saw_gann_without_tuesday=True
            if side is None:
                if saw_gann_without_tuesday:
                    blocked_tuesday+=1; rec['status']='BLOCKED_BY_TUESDAY_HIGH_LOW'
                else:
                    no_setup+=1; rec['status']='NO_5M_CLOSE_GANN_TRIGGER'
                attempts.append(rec); continue
            entry=float(entrybar.close); stop=float(sell if side=='LONG' else buy); target=float(entry+target_points if side=='LONG' else entry-target_points)
            post=b5[b5.date>entrybar.date].reset_index(drop=True)
            last5=b5.iloc[-1]
            ex=float(last5.close); ext=last5.date; reason='EOD'
            for _,bar in post.iterrows():
                c=float(bar.close)
                hit_sl=c<=stop if side=='LONG' else c>=stop
                hit_t=c>=target if side=='LONG' else c<=target
                if hit_sl:
                    ex=c; ext=bar.date; reason='SL'; break
                if hit_t:
                    ex=c; ext=bar.date; reason='TARGET'; break
            pts=(ex-entry) if side=='LONG' else (entry-ex)
            tr={**rec,'status':'TRADE','side':side,'touch_time':str(touch['time']),'entry_time':str(entrybar.date),'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),'exit_time':str(ext),'exit':round(float(ex),2),'reason':reason,'points':round(float(pts),2)}
            trades.append(tr); attempts.append(tr); traded=True
    return _summary(trades,week_count,skipped_gap,no_setup,blocked_tuesday),trades,attempts
