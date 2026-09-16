from __future__ import annotations
import pandas as pd
from . import strategy_lab_base as base


def _is_original_immediate(cfg):
    def eqnum(k,v):
        try:return abs(float(cfg.get(k,v))-float(v))<1e-9
        except:return False
    return (
        int(cfg.get('touch_interval',5))==5 and
        eqnum('wma_factor',.382) and eqnum('gann_step',.125) and
        cfg.get('touch_side','both')=='both' and cfg.get('entry_mode','trigger')=='trigger' and
        cfg.get('direction','both')=='both' and cfg.get('same_bar_policy','exclude')=='exclude' and
        cfg.get('target_mode','points')=='points' and eqnum('target_value',100) and
        cfg.get('stop_mode','gann')=='gann' and not bool(cfg.get('reentry',False)) and
        int(cfg.get('max_trades_per_day') or 1)==1 and
        str(cfg.get('start_time','09:15'))=='09:15' and str(cfg.get('end_time','15:30'))=='15:30' and
        eqnum('cost_points',0) and eqnum('slippage_points',0)
    )


def run_original_immediate(df):
    d=base.norm(df); d['session']=d.date.dt.date
    sessions=[(k,v.drop(columns='session').reset_index(drop=True)) for k,v in d.groupby('session',sort=True)]
    out=[]; daily=[]
    stats={'test_days':max(0,len(sessions)-1),'no_touch':0,'touch_ambiguous':0,'no_trigger':0,'same_bar_both':0,'engine':'original_7750_immediate_trigger'}
    for di in range(1,len(sessions)):
        sdate,day=sessions[di]; _,prev=sessions[di-1]
        ph=float(prev.high.max()); pl=float(prev.low.min()); pc=float(prev.iloc[-1].close)
        w=(ph-pl)*.382; res=pc+w; sup=pc-w
        work=day.copy(); mins=work.date.dt.hour*60+work.date.dt.minute
        work=work.assign(_slot=((mins-(9*60+15))//5).astype(int))
        touch_type=None; touch_minute=None; touch_px=None; ambiguous=False
        for slot,g in work.groupby('_slot',sort=True):
            if slot<0: continue
            hit_res=float(g.high.max())>=res; hit_sup=float(g.low.min())<=sup
            if hit_res and hit_sup:
                stats['touch_ambiguous']+=1; ambiguous=True; break
            if hit_res or hit_sup:
                touch_type='RESISTANCE' if hit_res else 'SUPPORT'; touch_px=res if hit_res else sup
                for _,m in g.iterrows():
                    if (hit_res and float(m.high)>=touch_px) or (hit_sup and float(m.low)<=touch_px):
                        touch_minute=m.date; break
                break
        if ambiguous: continue
        if touch_type is None:
            stats['no_touch']+=1; continue
        buy,sell=base.gann(touch_px,.125)
        start=day.index[day.date>=touch_minute]
        if len(start)==0:
            stats['no_trigger']+=1; continue
        entry_i=None; side=None; entry=None
        for i in range(int(start[0]),len(day)):
            b=day.iloc[i]
            # Original pre-close execution: immediate entry when price reaches the Gann trigger.
            if float(b.high)>=buy:
                entry_i=i; side='LONG'; entry=float(buy); break
            if float(b.low)<=sell:
                entry_i=i; side='SHORT'; entry=float(sell); break
        if entry_i is None:
            stats['no_trigger']+=1; continue
        eb=day.iloc[entry_i]
        stop=float(sell if side=='LONG' else buy)
        target=float(entry+100 if side=='LONG' else entry-100)
        exit_px=None; exit_time=None; reason=None; amb=False
        # Begin exit checking from the following minute because the intraminute order
        # of trigger vs high/low is unknowable from OHLC alone.
        for i in range(entry_i+1,len(day)):
            b=day.iloc[i]
            hit_sl=float(b.low)<=stop if side=='LONG' else float(b.high)>=stop
            hit_t=float(b.high)>=target if side=='LONG' else float(b.low)<=target
            if hit_sl and hit_t:
                stats['same_bar_both']+=1; amb=True; break
            if hit_sl:
                exit_px=stop; exit_time=b.date; reason='SL'; break
            if hit_t:
                exit_px=target; exit_time=b.date; reason='TARGET'; break
        if amb: continue
        if exit_px is None:
            last=day.iloc[-1]; exit_px=float(last.close); exit_time=last.date; reason='EOD'
        pts=(exit_px-entry) if side=='LONG' else (entry-exit_px)
        tr={'date':str(sdate),'trade_no':1,'first_touch':touch_type,'touch_time':str(touch_minute),'reference_price':round(float(touch_px),2),'buy_above':buy,'sell_below':sell,'side':side,'entry_time':str(eb.date),'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),'exit_time':str(exit_time),'exit':round(float(exit_px),2),'reason':reason,'points':round(float(pts),2)}
        out.append(tr); daily.append({'date':str(sdate),'points':round(float(pts),2),'trades':1})
    return base._summary(out,stats),out,daily,base._monthly(out)


def run_lab(df,cfg):
    if _is_original_immediate(cfg):
        return run_original_immediate(df)
    return base.run_lab(df,cfg)
