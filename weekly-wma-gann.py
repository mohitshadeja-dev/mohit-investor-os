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

def _first_touch(day,resistance,support):
    for _,b in _bars5(day).iterrows():
        hr=float(b.high)>=resistance; hs=float(b.low)<=support
        if hr and hs:return {'ambiguous':True,'time':b.date}
        if hr or hs:return {'ambiguous':False,'type':'RESISTANCE' if hr else 'SUPPORT','level':float(resistance if hr else support),'time':b.date}
    return None

def _summary(trades,weeks,skipped_gap,no_setup,ambiguous):
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
        'skipped_gap_days':skipped_gap,'no_setup_days':no_setup,'ambiguous_touch_days':ambiguous,
        'wednesday_trades':sum(t['trade_day']=='WED' for t in trades),'thursday_trades':sum(t['trade_day']=='THU' for t in trades),'friday_trades':sum(t['trade_day']=='FRI' for t in trades),
        'long_points':round(sum(t['points'] for t in trades if t['side']=='LONG'),2),
        'short_points':round(sum(t['points'] for t in trades if t['side']=='SHORT'),2),
    }

def run_weekly(df,wma_factor=.382,gann_step=.125,target_points=100.0,gap_near_target_points=30.0,same_bar_policy='stop_first'):
    """Weekly WMA-Gann strategy kept separate from the Original 7750 baseline.

    Weekly sequence:
      Tuesday expiry anchor -> attempt Wednesday using Tuesday H/L/C.
      If no valid trade Wednesday, recalculate from Wednesday -> attempt Thursday.
      If still no trade, recalculate from Thursday -> attempt Friday.
      Entry: first 5-minute candle CLOSE beyond Gann Buy/Sell trigger after first WMA S/R touch.
      Target: 100 points from actual 5-minute close entry. SL: opposite Gann boundary.
      Maximum one completed/open trade for the whole week.

    Gap/near-target filter operationalization:
      After the first touch establishes Gann levels, skip the day when the opening price has already
      travelled to within `gap_near_target_points` of the theoretical 100-point target from a Gann trigger.
      Long skip: open >= BuyAbove + (target-gap_buffer).
      Short skip: open <= SellBelow - (target-gap_buffer).
    """
    d=_norm(df); d['session']=d.date.dt.date
    sessions={k:v.drop(columns='session').reset_index(drop=True) for k,v in d.groupby('session',sort=True)}
    dates=sorted(sessions)
    # group by ISO year/week
    groups={}
    for sd in dates:
        iso=pd.Timestamp(sd).isocalendar(); groups.setdefault((int(iso.year),int(iso.week)),[]).append(sd)
    trades=[]; attempts=[]; skipped_gap=no_setup=ambiguous=0; week_count=0
    for wk,wdates in sorted(groups.items()):
        # need an expiry anchor on/before Tuesday and at least one Wed-Fri candidate
        week_count+=1
        bywd={pd.Timestamp(x).weekday():x for x in wdates}
        # Tuesday=1. If Tuesday holiday, use latest available day Monday/Tuesday in that week.
        anchor_candidates=[x for x in wdates if pd.Timestamp(x).weekday()<=1]
        if not anchor_candidates: continue
        anchor=max(anchor_candidates)
        traded=False
        for wd,label in ((2,'WED'),(3,'THU'),(4,'FRI')):
            if traded: break
            cand=bywd.get(wd)
            if cand is None: continue
            # reference is immediate prior trading session before candidate, so Thu uses Wed; Fri uses Thu.
            prevs=[x for x in dates if x<cand]
            if not prevs: continue
            ref=max(prevs); prev=sessions[ref]; day=sessions[cand]
            ph=float(prev.high.max()); pl=float(prev.low.min()); pc=float(prev.iloc[-1].close)
            wma=(ph-pl)*float(wma_factor); resistance=pc+wma; support=pc-wma
            touch=_first_touch(day,resistance,support)
            rec={'week':f'{wk[0]}-W{wk[1]:02d}','trade_day':label,'date':str(cand),'reference_date':str(ref),'support':round(support,2),'resistance':round(resistance,2)}
            if touch is None:
                no_setup+=1; rec['status']='NO_TOUCH'; attempts.append(rec); continue
            if touch.get('ambiguous'):
                ambiguous+=1; rec['status']='AMBIGUOUS_TOUCH'; attempts.append(rec); continue
            buy,sell=_gann(touch['level'],float(gann_step)); rec.update({'first_touch':touch['type'],'buy_above':buy,'sell_below':sell})
            op=float(day.iloc[0].open); travel=max(0.0,float(target_points)-float(gap_near_target_points))
            near_long=op>=buy+travel; near_short=op<=sell-travel
            if near_long or near_short:
                skipped_gap+=1; rec.update({'status':'GAP_NEAR_TARGET_SKIP','open':round(op,2)}); attempts.append(rec); continue
            b5=_bars5(day); b5=b5[b5.date>=touch['time']].reset_index(drop=True)
            entrybar=None; side=None
            for _,b in b5.iterrows():
                c=float(b.close)
                if c>=buy: entrybar=b; side='LONG'; break
                if c<=sell: entrybar=b; side='SHORT'; break
            if side is None:
                no_setup+=1; rec['status']='NO_5M_CLOSE_TRIGGER'; attempts.append(rec); continue
            entry=float(entrybar.close); stop=float(sell if side=='LONG' else buy); target=float(entry+target_points if side=='LONG' else entry-target_points)
            # entry occurs at 5m close; use subsequent 1m candles for target/SL ordering.
            minute=day[day.date>entrybar.date].reset_index(drop=True)
            ex=float(day.iloc[-1].close); ext=day.iloc[-1].date; reason='EOD'
            for _,m in minute.iterrows():
                hsl=float(m.low)<=stop if side=='LONG' else float(m.high)>=stop
                ht=float(m.high)>=target if side=='LONG' else float(m.low)<=target
                if hsl and ht:
                    if same_bar_policy=='target_first': ex=target; reason='TARGET'
                    else: ex=stop; reason='SL'
                    ext=m.date; break
                if hsl: ex=stop; ext=m.date; reason='SL'; break
                if ht: ex=target; ext=m.date; reason='TARGET'; break
            pts=(ex-entry) if side=='LONG' else (entry-ex)
            tr={**rec,'status':'TRADE','side':side,'touch_time':str(touch['time']),'entry_time':str(entrybar.date),'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),'exit_time':str(ext),'exit':round(float(ex),2),'reason':reason,'points':round(float(pts),2),'open':round(op,2)}
            trades.append(tr); attempts.append(tr); traded=True
    return _summary(trades,week_count,skipped_gap,no_setup,ambiguous),trades,attempts
