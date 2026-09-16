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
        cfg.get('direction','both')=='both' and cfg.get('target_mode','points')=='points' and eqnum('target_value',100) and
        cfg.get('stop_mode','gann') in ('gann','gann_close_1m','gann_close_5m','gann_touch_entrybar') and
        not bool(cfg.get('reentry',False)) and int(cfg.get('max_trades_per_day') or 1)==1 and
        str(cfg.get('start_time','09:15'))=='09:15' and str(cfg.get('end_time','15:30'))=='15:30' and
        eqnum('cost_points',0) and eqnum('slippage_points',0)
    )


def _is_opposite_recalc(cfg):
    def eqnum(k,v):
        try:return abs(float(cfg.get(k,v))-float(v))<1e-9
        except:return False
    return (
        int(cfg.get('touch_interval',5))==5 and
        eqnum('wma_factor',.382) and eqnum('gann_step',.125) and
        cfg.get('touch_side')=='both_recalc' and cfg.get('entry_mode')=='trigger' and
        cfg.get('direction','both')=='both' and cfg.get('target_mode','points')=='points' and eqnum('target_value',100) and
        cfg.get('stop_mode','gann')=='gann' and bool(cfg.get('reentry',False)) and
        int(cfg.get('max_trades_per_day') or 2)==2 and
        str(cfg.get('start_time','09:15'))=='09:15' and str(cfg.get('end_time','15:30'))=='15:30' and
        eqnum('cost_points',0) and eqnum('slippage_points',0)
    )


def _augment(summary,trades):
    summary=dict(summary)
    summary['long_trades']=sum(t['side']=='LONG' for t in trades)
    summary['short_trades']=sum(t['side']=='SHORT' for t in trades)
    summary['support_first']=sum(t.get('first_touch')=='SUPPORT' for t in trades if t.get('trade_no')==1)
    summary['resistance_first']=sum(t.get('first_touch')=='RESISTANCE' for t in trades if t.get('trade_no')==1)
    summary['opposite_recalc_trades']=sum(t.get('reference_phase')=='OPPOSITE_RECALC' for t in trades)
    return summary


def _decode_policies(same_bar_policy):
    if same_bar_policy.startswith('entry_'):
        raw=same_bar_policy[6:]
        if '__' in raw: entry_policy,exit_policy=raw.split('__',1)
        else: entry_policy,exit_policy=raw,'stop_first'
        return entry_policy,exit_policy
    return 'candle_direction',same_bar_policy


def _find_first_touch(day,res,sup,start_time=None,both=True,target_type=None):
    work=day if start_time is None else day[day.date>start_time].reset_index(drop=True)
    if work.empty:return None
    if target_type in ('RESISTANCE','SUPPORT'):
        level=res if target_type=='RESISTANCE' else sup
        for _,m in work.iterrows():
            if target_type=='RESISTANCE' and float(m.high)>=level:
                return {'type':'RESISTANCE','level':float(level),'time':m.date}
            if target_type=='SUPPORT' and float(m.low)<=level:
                return {'type':'SUPPORT','level':float(level),'time':m.date}
        return None
    mins=work.date.dt.hour*60+work.date.dt.minute
    work=work.assign(_slot=((mins-(9*60+15))//5).astype(int))
    for slot,g in work.groupby('_slot',sort=True):
        if slot<0:continue
        hit_res=float(g.high.max())>=res; hit_sup=float(g.low.min())<=sup
        if hit_res and hit_sup:return {'ambiguous':True,'time':g.iloc[0].date}
        if hit_res or hit_sup:
            typ='RESISTANCE' if hit_res else 'SUPPORT'; level=res if hit_res else sup
            for _,m in g.iterrows():
                if (hit_res and float(m.high)>=level) or (hit_sup and float(m.low)<=level):
                    return {'ambiguous':False,'type':typ,'level':float(level),'time':m.date}
    return None


def _trade_from_reference(day,start_time,ref_type,ref_price,trade_no,phase):
    buy,sell=base.gann(ref_price,.125)
    work=day[day.date>=start_time].reset_index(drop=True)
    entry_i=None; side=None; entry=None
    for i,b in work.iterrows():
        long_hit=float(b.high)>=buy; short_hit=float(b.low)<=sell
        if long_hit and short_hit:
            # deterministic tie-break only when one minute spans both triggers
            if float(b.close)>=float(b.open): side='LONG'; entry=float(buy)
            else: side='SHORT'; entry=float(sell)
            entry_i=i; break
        if long_hit:side='LONG'; entry=float(buy); entry_i=i; break
        if short_hit:side='SHORT'; entry=float(sell); entry_i=i; break
    if entry_i is None:return None
    eb=work.iloc[entry_i]; stop=float(sell if side=='LONG' else buy); target=float(entry+100 if side=='LONG' else entry-100)
    ex=None; ext=None; reason=None
    for i in range(entry_i+1,len(work)):
        b=work.iloc[i]
        hsl=float(b.low)<=stop if side=='LONG' else float(b.high)>=stop
        ht=float(b.high)>=target if side=='LONG' else float(b.low)<=target
        if hsl and ht:
            ex=stop; ext=b.date; reason='SL'; break
        if hsl:ex=stop; ext=b.date; reason='SL'; break
        if ht:ex=target; ext=b.date; reason='TARGET'; break
    if ex is None:
        last=work.iloc[-1]; ex=float(last.close); ext=last.date; reason='EOD'
    pts=(ex-entry) if side=='LONG' else (entry-ex)
    return {
        'trade_no':trade_no,'reference_phase':phase,'first_touch':ref_type,'touch_time':str(start_time),
        'reference_price':round(float(ref_price),2),'buy_above':buy,'sell_below':sell,'side':side,
        'entry_time':str(eb.date),'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),
        'exit_time':str(ext),'exit':round(float(ex),2),'reason':reason,'points':round(float(pts),2),
        '_exit_ts':ext
    }


def run_opposite_wma_recalc(df):
    d=base.norm(df); d['session']=d.date.dt.date
    sessions=[(k,v.drop(columns='session').reset_index(drop=True)) for k,v in d.groupby('session',sort=True)]
    out=[]; daily=[]
    stats={'test_days':max(0,len(sessions)-1),'no_touch':0,'touch_ambiguous':0,'no_trigger':0,'same_bar_both':0,'engine':'opposite_wma_recalc_exact_trigger'}
    for di in range(1,len(sessions)):
        sdate,day=sessions[di]; _,prev=sessions[di-1]
        ph=float(prev.high.max()); pl=float(prev.low.min()); pc=float(prev.iloc[-1].close)
        w=(ph-pl)*.382; res=pc+w; sup=pc-w
        first=_find_first_touch(day,res,sup)
        if first is None:stats['no_touch']+=1;continue
        if first.get('ambiguous'):stats['touch_ambiguous']+=1;continue
        t1=_trade_from_reference(day,first['time'],first['type'],first['level'],1,'FIRST_WMA')
        day_pts=0; day_trades=0
        if t1 is None:
            stats['no_trigger']+=1;continue
        t1['date']=str(sdate); exit1=t1.pop('_exit_ts'); out.append(t1); day_pts+=t1['points']; day_trades+=1
        # Only after the first trade has finished do we look for the opposite WMA level.
        if t1['reason']!='EOD':
            opposite='SUPPORT' if first['type']=='RESISTANCE' else 'RESISTANCE'
            opp=_find_first_touch(day,res,sup,start_time=exit1,target_type=opposite)
            if opp is not None:
                t2=_trade_from_reference(day,opp['time'],opp['type'],opp['level'],2,'OPPOSITE_RECALC')
                if t2 is not None:
                    t2['date']=str(sdate); t2.pop('_exit_ts',None); out.append(t2); day_pts+=t2['points']; day_trades+=1
        daily.append({'date':str(sdate),'points':round(float(day_pts),2),'trades':day_trades})
    summary=base._summary(out,stats)
    return _augment(summary,out),out,daily,base._monthly(out)


def run_original_immediate(df, stop_rule='gann', same_bar_policy='stop_first'):
    entry_tie_policy,exit_same_bar_policy=_decode_policies(str(same_bar_policy))
    d=base.norm(df); d['session']=d.date.dt.date
    sessions=[(k,v.drop(columns='session').reset_index(drop=True)) for k,v in d.groupby('session',sort=True)]
    out=[]; daily=[]
    stats={'test_days':max(0,len(sessions)-1),'no_touch':0,'touch_ambiguous':0,'no_trigger':0,'same_bar_both':0,'dual_trigger_entries':0,'dual_trigger_skips':0,'engine':'original_exact_trigger_'+stop_rule+'_'+entry_tie_policy+'_'+exit_same_bar_policy}
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
            long_hit=float(b.high)>=buy; short_hit=float(b.low)<=sell
            if long_hit and short_hit:
                stats['dual_trigger_entries']+=1
                if entry_tie_policy=='skip':
                    stats['dual_trigger_skips']+=1; entry_i=None; side=None; entry=None
                    break
                elif entry_tie_policy=='long_first':
                    entry_i=i; side='LONG'; entry=float(buy)
                elif entry_tie_policy=='short_first':
                    entry_i=i; side='SHORT'; entry=float(sell)
                else:
                    if float(b.close)>=float(b.open): entry_i=i; side='LONG'; entry=float(buy)
                    else: entry_i=i; side='SHORT'; entry=float(sell)
                break
            if long_hit: entry_i=i; side='LONG'; entry=float(buy); break
            if short_hit: entry_i=i; side='SHORT'; entry=float(sell); break
        if entry_i is None:
            stats['no_trigger']+=1; continue
        eb=day.iloc[entry_i]
        stop=float(sell if side=='LONG' else buy)
        target=float(entry+100 if side=='LONG' else entry-100)
        exit_px=None; exit_time=None; reason=None; drop_trade=False
        first_exit_i=entry_i if stop_rule=='gann_touch_entrybar' else entry_i+1
        for i in range(first_exit_i,len(day)):
            b=day.iloc[i]
            hit_target=float(b.high)>=target if side=='LONG' else float(b.low)<=target
            if stop_rule in ('gann','gann_touch_entrybar'):
                hit_stop=float(b.low)<=stop if side=='LONG' else float(b.high)>=stop
            elif stop_rule=='gann_close_1m':
                hit_stop=float(b.close)<=stop if side=='LONG' else float(b.close)>=stop
            else:
                minute_of_day=int(b.date.hour)*60+int(b.date.minute)
                slot=(minute_of_day-(9*60+15))//5
                bucket_end=(9*60+15)+(slot+1)*5-1
                hit_stop=False
                if minute_of_day==bucket_end or i==len(day)-1:
                    hit_stop=float(b.close)<=stop if side=='LONG' else float(b.close)>=stop
            if hit_stop and hit_target:
                stats['same_bar_both']+=1
                if exit_same_bar_policy=='target_first': exit_px=target; exit_time=b.date; reason='TARGET'
                elif exit_same_bar_policy=='exclude': drop_trade=True
                else: exit_px=stop; exit_time=b.date; reason='SL'
                break
            if hit_stop:
                exit_px=stop; exit_time=b.date; reason='SL'; break
            if hit_target:
                exit_px=target; exit_time=b.date; reason='TARGET'; break
        if drop_trade: continue
        if exit_px is None:
            last=day.iloc[-1]; exit_px=float(last.close); exit_time=last.date; reason='EOD'
        pts=(exit_px-entry) if side=='LONG' else (entry-exit_px)
        tr={'date':str(sdate),'trade_no':1,'first_touch':touch_type,'touch_time':str(touch_minute),'reference_price':round(float(touch_px),2),'buy_above':buy,'sell_below':sell,'side':side,'entry_time':str(eb.date),'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),'exit_time':str(exit_time),'exit':round(float(exit_px),2),'reason':reason,'points':round(float(pts),2)}
        out.append(tr); daily.append({'date':str(sdate),'points':round(float(pts),2),'trades':1})
    summary=base._summary(out,stats)
    return _augment(summary,out),out,daily,base._monthly(out)


def run_lab(df,cfg):
    if _is_opposite_recalc(cfg):
        return run_opposite_wma_recalc(df)
    if _is_original_immediate(cfg):
        return run_original_immediate(df,cfg.get('stop_mode','gann'),cfg.get('same_bar_policy','stop_first'))
    return base.run_lab(df,cfg)
