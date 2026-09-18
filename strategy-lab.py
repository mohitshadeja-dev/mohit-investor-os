from __future__ import annotations
from datetime import time
from math import floor, sqrt
import pandas as pd

IST='Asia/Kolkata'

def norm(df):
    d=df.copy(); d.columns=[str(c).lower() for c in d.columns]
    d['date']=pd.to_datetime(d['date'],errors='coerce')
    if d['date'].dt.tz is None: d['date']=d['date'].dt.tz_localize(IST)
    else: d['date']=d['date'].dt.tz_convert(IST)
    for c in ('open','high','low','close'): d[c]=pd.to_numeric(d[c],errors='coerce')
    d=d.dropna(subset=['date','open','high','low','close']).sort_values('date')
    lt=d.date.dt.time
    return d[(lt>=time(9,15))&(lt<=time(15,30))].reset_index(drop=True)

def gann(price,step=.125):
    r=sqrt(float(price)); n=floor((r+1e-10)/step)
    return round(((n+1)*step)**2,2), round((n*step)**2,2)

def bars(day,minutes):
    if int(minutes)<=1: return day.copy()
    x=day.set_index('date')
    return x.resample(f'{int(minutes)}min',origin='start_day',offset='15min').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last')).dropna().reset_index()

def first_touch(day,resistance,support,minutes=5,side_filter='both'):
    minutes=int(minutes); b=bars(day,minutes)
    for _,r in b.iterrows():
        tr=float(r.high)>=resistance and side_filter in ('both','resistance')
        ts=float(r.low)<=support and side_filter in ('both','support')
        if tr and ts:return {'ambiguous':True,'time':r.date}
        if tr or ts:
            typ='RESISTANCE' if tr else 'SUPPORT'; level=float(resistance if tr else support)
            if minutes<=1: touch_minute=r.date
            else:
                bucket_start=r.date; bucket_end=bucket_start+pd.Timedelta(minutes=minutes)
                raw=day[(day.date>=bucket_start)&(day.date<bucket_end)]
                touch_minute=bucket_start
                for _,m in raw.iterrows():
                    if (tr and float(m.high)>=level) or (ts and float(m.low)<=level):
                        touch_minute=m.date; break
            return {'ambiguous':False,'type':typ,'level':level,'time':touch_minute,'bucket_time':r.date}
    return None

def _summary(out,stats):
    pts=[t['points'] for t in out]; wins=[p for p in pts if p>0]; losses=[p for p in pts if p<0]
    total=sum(pts); grossp=sum(wins); grossl=abs(sum(losses)); eq=peak=dd=0; streak=cur=0
    for p in pts:
        eq+=p; peak=max(peak,eq); dd=max(dd,peak-eq); cur=cur+1 if p<0 else 0; streak=max(streak,cur)
    longs=[t['points'] for t in out if t['side']=='LONG']; shorts=[t['points'] for t in out if t['side']=='SHORT']
    return {**stats,'trades':len(out),'wins':len(wins),'losses':len(losses),'win_rate':round(100*len(wins)/len(out),2) if out else 0,'total_points':round(total,2),'gross_profit':round(grossp,2),'gross_loss':round(grossl,2),'profit_factor':round(grossp/grossl,2) if grossl else (999 if grossp else 0),'avg_points':round(total/len(out),2) if out else 0,'max_drawdown_points':round(dd,2),'max_losing_streak':streak,'targets':sum(t['reason']=='TARGET' for t in out),'stops':sum(t['reason']=='SL' for t in out),'eod':sum(t['reason']=='EOD' for t in out),'long_points':round(sum(longs),2),'short_points':round(sum(shorts),2),'best_trade':round(max(pts),2) if pts else 0,'worst_trade':round(min(pts),2) if pts else 0}

def _monthly(out):
    m={}
    for t in out:m[t['date'][:7]]=m.get(t['date'][:7],0)+t['points']
    return [{'month':k,'points':round(v,2)} for k,v in sorted(m.items())]

def _is_original_7750(cfg):
    def eqnum(k,v):
        try:return abs(float(cfg.get(k,v))-float(v))<1e-9
        except:return False
    return (
        int(cfg.get('touch_interval',5))==5 and int(cfg.get('confirm_interval',1))==1 and
        eqnum('wma_factor',.382) and eqnum('gann_step',.125) and
        cfg.get('touch_side','both')=='both' and cfg.get('entry_mode','close')=='close' and
        cfg.get('direction','both')=='both' and cfg.get('same_bar_policy','exclude')=='exclude' and
        cfg.get('target_mode','points')=='points' and eqnum('target_value',100) and
        cfg.get('stop_mode','gann')=='gann' and not bool(cfg.get('reentry',False)) and
        int(cfg.get('max_trades_per_day') or 1)==1 and
        str(cfg.get('start_time','09:15'))=='09:15' and str(cfg.get('end_time','15:30'))=='15:30' and
        eqnum('cost_points',0) and eqnum('slippage_points',0)
    )

def run_original_7750(df):
    d=norm(df); d['session']=d.date.dt.date
    sessions=[(k,v.drop(columns='session').reset_index(drop=True)) for k,v in d.groupby('session',sort=True)]
    out=[]; daily=[]; stats={'test_days':max(0,len(sessions)-1),'no_touch':0,'touch_ambiguous':0,'no_trigger':0,'same_bar_both':0,'engine':'original_7750_exact'}
    for di in range(1,len(sessions)):
        sdate,day=sessions[di]; _,prev=sessions[di-1]
        ph=float(prev.high.max()); pl=float(prev.low.min()); pc=float(prev.iloc[-1].close); w=(ph-pl)*.382; res=pc+w; sup=pc-w
        work=day.copy(); mins=work.date.dt.hour*60+work.date.dt.minute; work=work.assign(_slot=((mins-(9*60+15))//5).astype(int))
        touch_type=None; touch_minute=None; touch_px=None; ambiguous=False
        for slot,g in work.groupby('_slot',sort=True):
            if slot<0: continue
            tr=float(g.high.max())>=res; ts=float(g.low.min())<=sup
            if tr and ts: stats['touch_ambiguous']+=1; ambiguous=True; break
            if tr or ts:
                touch_type='RESISTANCE' if tr else 'SUPPORT'; touch_px=res if tr else sup
                for _,m in g.iterrows():
                    if (tr and float(m.high)>=touch_px) or (ts and float(m.low)<=touch_px): touch_minute=m.date; break
                break
        if ambiguous: continue
        if touch_type is None: stats['no_touch']+=1; continue
        buy,sell=gann(touch_px,.125); start_idx=day.index[day.date>=touch_minute]
        if len(start_idx)==0: stats['no_trigger']+=1; continue
        entry_i=None; side=None
        for i in range(int(start_idx[0]),len(day)):
            b=day.iloc[i]; c=float(b.close)
            if c>=buy: entry_i=i; side='LONG'; break
            if c<=sell: entry_i=i; side='SHORT'; break
        if entry_i is None: stats['no_trigger']+=1; continue
        eb=day.iloc[entry_i]; entry=float(eb.close); stop=sell if side=='LONG' else buy; target=entry+100 if side=='LONG' else entry-100
        exit_px=None; exit_time=None; reason=None; amb=False
        for i in range(entry_i+1,len(day)):
            b=day.iloc[i]; hit_sl=float(b.low)<=stop if side=='LONG' else float(b.high)>=stop; hit_t=float(b.high)>=target if side=='LONG' else float(b.low)<=target
            if hit_sl and hit_t: stats['same_bar_both']+=1; amb=True; break
            if hit_sl: exit_px=stop; exit_time=b.date; reason='SL'; break
            if hit_t: exit_px=target; exit_time=b.date; reason='TARGET'; break
        if amb: continue
        if exit_px is None:
            last=day.iloc[-1]; exit_px=float(last.close); exit_time=last.date; reason='EOD'
        pts=(exit_px-entry) if side=='LONG' else (entry-exit_px)
        tr={'date':str(sdate),'trade_no':1,'first_touch':touch_type,'touch_time':str(touch_minute),'reference_price':round(float(touch_px),2),'buy_above':buy,'sell_below':sell,'side':side,'entry_time':str(eb.date),'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),'exit_time':str(exit_time),'exit':round(float(exit_px),2),'reason':reason,'points':round(float(pts),2)}
        out.append(tr); daily.append({'date':str(sdate),'points':round(float(pts),2),'trades':1})
    return _summary(out,stats),out,daily,_monthly(out)

def _signal(bar,side,buy,sell,mode='close'):
    if mode in ('wick','trigger','marketable'):
        return float(bar.high)>=buy if side=='LONG' else float(bar.low)<=sell
    return float(bar.close)>=buy if side=='LONG' else float(bar.close)<=sell

def run_lab(df,cfg):
    if _is_original_7750(cfg): return run_original_7750(df)
    d=norm(df); d['session']=d.date.dt.date
    sessions=[(k,v.drop(columns='session').reset_index(drop=True)) for k,v in d.groupby('session',sort=True)]
    out=[]; daily=[]; stats={'test_days':max(0,len(sessions)-1),'no_touch':0,'touch_ambiguous':0,'no_trigger':0,'same_bar_both':0,'engine':'flexible_lab'}
    wma_factor=float(cfg.get('wma_factor',.382)); touch_int=int(cfg.get('touch_interval',5)); confirm_int=int(cfg.get('confirm_interval',1)); gstep=float(cfg.get('gann_step',.125))
    target_mode=cfg.get('target_mode','points'); target_value=float(cfg.get('target_value',100)); stop_mode=cfg.get('stop_mode','gann'); stop_value=float(cfg.get('stop_value',0) or 0)
    reentry=bool(cfg.get('reentry',True)); maxtr=cfg.get('max_trades_per_day'); maxtr=int(maxtr) if maxtr not in (None,'',0) else None
    same=cfg.get('same_bar_policy','stop_first'); direction=cfg.get('direction','both'); entry_mode=cfg.get('entry_mode','close'); touch_side=cfg.get('touch_side','both')
    cost=float(cfg.get('cost_points',0) or 0); slip=float(cfg.get('slippage_points',0) or 0)
    st=cfg.get('start_time','09:15'); et=cfg.get('end_time','15:30'); sh,sm=map(int,st.split(':')); eh,em=map(int,et.split(':'))
    for di in range(1,len(sessions)):
        sdate,day=sessions[di]; _,prev=sessions[di-1]; day=day[(day.date.dt.time>=time(sh,sm))&(day.date.dt.time<=time(eh,em))].reset_index(drop=True)
        if day.empty: continue
        ph=float(prev.high.max()); pl=float(prev.low.min()); pc=float(prev.iloc[-1].close); wma=(ph-pl)*wma_factor; res=pc+wma; sup=pc-wma
        touch=first_touch(day,res,sup,touch_int,touch_side)
        if touch is None: stats['no_touch']+=1; continue
        if touch.get('ambiguous'): stats['touch_ambiguous']+=1; continue
        buy,sell=gann(touch['level'],gstep); sigbars=bars(day,confirm_int); sigbars=sigbars[sigbars.date>=touch['time']].reset_index(drop=True)
        n=0; cursor_time=touch['time']; had=False; day_pts=0
        # Initially both directions may trigger. After ANY completed trade, both directions are disarmed.
        # LONG re-arms only after price moves back below Buy Above; SHORT re-arms only after price
        # moves back above Sell Below. This guarantees every re-entry is a fresh crossing, not a stale level.
        armed={'LONG':True,'SHORT':True}
        while True:
            if maxtr is not None and n>=maxtr: break
            cand=sigbars[sigbars.date>=cursor_time]; entrybar=None; side=None
            for _,b in cand.iterrows():
                c=float(b.close)
                if not armed['LONG'] and c<buy: armed['LONG']=True
                if not armed['SHORT'] and c>sell: armed['SHORT']=True
                checks=[]
                if direction in ('both','long'): checks.append('LONG')
                if direction in ('both','short'): checks.append('SHORT')
                for sd in checks:
                    if armed[sd] and _signal(b,sd,buy,sell,entry_mode):
                        entrybar=b; side=sd; break
                if side: break
            if side is None:
                if not had: stats['no_trigger']+=1
                break
            had=True; n+=1
            if entry_mode=='trigger': entry=float(buy if side=='LONG' else sell)
            elif entry_mode=='marketable':
                # If the tradable bar opens beyond the stop trigger, the old
                # trigger price is unavailable. Fill at that bar's open.
                bar_open=float(entrybar.open)
                if side=='LONG': entry=max(float(buy),bar_open)+slip
                else: entry=min(float(sell),bar_open)-slip
            else: entry=float(entrybar.close)+(slip if side=='LONG' else -slip)
            if stop_mode=='gann': stop=sell if side=='LONG' else buy
            elif stop_mode=='points': stop=entry-stop_value if side=='LONG' else entry+stop_value
            else: stop=entry*(1-stop_value/100) if side=='LONG' else entry*(1+stop_value/100)
            risk=abs(entry-stop)
            if target_mode=='points': target=entry+target_value if side=='LONG' else entry-target_value
            elif target_mode=='percent': target=entry*(1+target_value/100) if side=='LONG' else entry*(1-target_value/100)
            else: target=entry+risk*target_value if side=='LONG' else entry-risk*target_value
            minute=day[day.date>entrybar.date].reset_index(drop=True); reason='EOD'; ex=float(day.iloc[-1].close); ext=day.iloc[-1].date
            for _,m in minute.iterrows():
                hsl=float(m.low)<=stop if side=='LONG' else float(m.high)>=stop; ht=float(m.high)>=target if side=='LONG' else float(m.low)<=target
                if hsl and ht:
                    stats['same_bar_both']+=1
                    if same=='exclude': reason='AMBIGUOUS'; ex=None; ext=m.date
                    elif same=='target_first': reason='TARGET'; ex=target; ext=m.date
                    else: reason='SL'; ex=stop; ext=m.date
                    break
                if hsl: reason='SL'; ex=stop; ext=m.date; break
                if ht: reason='TARGET'; ex=target; ext=m.date; break
            # Critical re-entry rule: after ANY trade, BOTH directions must reset before either
            # can enter again. Example: a LONG that stops below Sell Below cannot instantly become
            # a SHORT at the stale Sell Below trigger. SHORT must first trade back above Sell Below,
            # then break it again. Likewise LONG must first trade below Buy Above, then break it again.
            armed={'LONG':False,'SHORT':False}
            if reason!='AMBIGUOUS':
                pts=((ex-entry) if side=='LONG' else (entry-ex))-cost
                tr={'date':str(sdate),'trade_no':n,'first_touch':touch['type'],'touch_time':str(touch['time']),'reference_price':round(touch['level'],2),'buy_above':buy,'sell_below':sell,'side':side,'entry_time':str(entrybar.date),'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),'exit_time':str(ext),'exit':round(ex,2),'reason':reason,'points':round(pts,2)}
                out.append(tr); day_pts+=pts
            if not reentry or reason=='EOD' or ex is None: break
            cursor_time=ext+pd.Timedelta(seconds=1)
        daily.append({'date':str(sdate),'points':round(day_pts,2),'trades':n})
    return _summary(out,stats),out,daily,_monthly(out)
