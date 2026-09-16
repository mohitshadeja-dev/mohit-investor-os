from __future__ import annotations
from datetime import time
import pandas as pd
from . import strategy_lab_base as base


def _bars(day, minutes):
    minutes=max(1,int(minutes))
    if minutes==1:return day.copy()
    x=day.set_index('date')
    return x.resample(f'{minutes}min',origin='start_day',offset='15min').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last')).dropna().reset_index()


def _first_touch(day,res,sup,touch_interval,start_after=None,target_type=None):
    work=day if start_after is None else day[day.date>start_after].reset_index(drop=True)
    if work.empty:return None
    b=_bars(work,touch_interval)
    for _,r in b.iterrows():
        hr=float(r.high)>=res; hs=float(r.low)<=sup
        if target_type=='RESISTANCE': hs=False
        if target_type=='SUPPORT': hr=False
        if hr and hs:return {'ambiguous':True,'time':r.date}
        if hr or hs:
            typ='RESISTANCE' if hr else 'SUPPORT'; level=float(res if hr else sup)
            raw=work[(work.date>=r.date)&(work.date<r.date+pd.Timedelta(minutes=max(1,int(touch_interval))))]
            t=r.date
            for _,m in raw.iterrows():
                if (typ=='RESISTANCE' and float(m.high)>=level) or (typ=='SUPPORT' and float(m.low)<=level):
                    t=m.date;break
            return {'ambiguous':False,'type':typ,'level':level,'time':t}
    return None


def _mapped_side(ref_type):
    return 'LONG' if ref_type=='RESISTANCE' else 'SHORT'


def _entry_hit(bar,side,buy,sell,entry_mode):
    if entry_mode in ('trigger','wick'):
        return float(bar.high)>=buy if side=='LONG' else float(bar.low)<=sell
    return float(bar.close)>=buy if side=='LONG' else float(bar.close)<=sell


def _trade(day,ref,cfg,trade_no,phase):
    side=_mapped_side(ref['type'])
    direction=cfg.get('direction','both')
    if direction=='long' and side!='LONG':return None
    if direction=='short' and side!='SHORT':return None
    buy,sell=base.gann(ref['level'],float(cfg.get('gann_step',.125)))
    confirm=int(cfg.get('confirm_interval',1)); entry_mode=cfg.get('entry_mode','trigger')
    sig=_bars(day[day.date>=ref['time']].reset_index(drop=True),confirm)
    entrybar=None
    for _,b in sig.iterrows():
        if _entry_hit(b,side,buy,sell,entry_mode):entrybar=b;break
    if entrybar is None:return None
    slip=float(cfg.get('slippage_points',0) or 0)
    if entry_mode=='trigger':entry=float(buy if side=='LONG' else sell)
    else:entry=float(entrybar.close)+(slip if side=='LONG' else -slip)
    stop_mode=cfg.get('stop_mode','gann'); stop_value=float(cfg.get('stop_value',0) or 0)
    if stop_mode=='gann':stop=float(sell if side=='LONG' else buy)
    elif stop_mode=='points':stop=entry-stop_value if side=='LONG' else entry+stop_value
    else:stop=entry*(1-stop_value/100) if side=='LONG' else entry*(1+stop_value/100)
    target_mode=cfg.get('target_mode','points'); tv=float(cfg.get('target_value',100) or 100); risk=abs(entry-stop)
    if target_mode=='points':target=entry+tv if side=='LONG' else entry-tv
    elif target_mode=='percent':target=entry*(1+tv/100) if side=='LONG' else entry*(1-tv/100)
    else:target=entry+risk*tv if side=='LONG' else entry-risk*tv
    same=cfg.get('same_bar_policy','stop_first'); cost=float(cfg.get('cost_points',0) or 0)
    minute=day[day.date>entrybar.date].reset_index(drop=True); ex=float(day.iloc[-1].close); ext=day.iloc[-1].date; reason='EOD'
    for _,m in minute.iterrows():
        hsl=float(m.low)<=stop if side=='LONG' else float(m.high)>=stop
        ht=float(m.high)>=target if side=='LONG' else float(m.low)<=target
        if hsl and ht:
            if same=='exclude':return None
            if same=='target_first':ex=target;reason='TARGET'
            else:ex=stop;reason='SL'
            ext=m.date;break
        if hsl:ex=stop;ext=m.date;reason='SL';break
        if ht:ex=target;ext=m.date;reason='TARGET';break
    pts=((ex-entry) if side=='LONG' else (entry-ex))-cost
    return {'trade_no':trade_no,'reference_phase':phase,'first_touch':ref['type'],'touch_time':str(ref['time']),'reference_price':round(float(ref['level']),2),'buy_above':buy,'sell_below':sell,'side':side,'entry_time':str(entrybar.date),'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),'exit_time':str(ext),'exit':round(float(ex),2),'reason':reason,'points':round(float(pts),2),'_exit_ts':ext}


def run_same_day_sr(df,cfg):
    d=base.norm(df);d['session']=d.date.dt.date
    sessions=[(k,v.drop(columns='session').reset_index(drop=True)) for k,v in d.groupby('session',sort=True)]
    out=[];daily=[];stats={'test_days':max(0,len(sessions)-1),'no_touch':0,'touch_ambiguous':0,'no_trigger':0,'same_bar_both':0,'engine':'same_day_sr_editable'}
    wma=float(cfg.get('wma_factor',.382)); touch_interval=int(cfg.get('touch_interval',5)); maxtr=cfg.get('max_trades_per_day'); maxtr=int(maxtr) if maxtr not in (None,'',0) else 999
    sh,sm=map(int,str(cfg.get('start_time','09:15')).split(':'));eh,em=map(int,str(cfg.get('end_time','15:30')).split(':'))
    for di in range(1,len(sessions)):
        sdate,day=sessions[di];_,prev=sessions[di-1]
        day=day[(day.date.dt.time>=time(sh,sm))&(day.date.dt.time<=time(eh,em))].reset_index(drop=True)
        if day.empty:continue
        ph=float(prev.high.max());pl=float(prev.low.min());pc=float(prev.iloc[-1].close);wm=(ph-pl)*wma;res=pc+wm;sup=pc-wm
        first=_first_touch(day,res,sup,touch_interval)
        if first is None:stats['no_touch']+=1;continue
        if first.get('ambiguous'):stats['touch_ambiguous']+=1;continue
        day_pts=0;count=0
        t1=_trade(day,first,cfg,1,'FIRST_WMA')
        if t1 is None:stats['no_trigger']+=1
        else:
            t1['date']=str(sdate);exit1=t1.pop('_exit_ts');out.append(t1);day_pts+=t1['points'];count+=1
            if count<maxtr and cfg.get('touch_side')=='both_recalc' and t1['reason']!='EOD':
                opposite='SUPPORT' if first['type']=='RESISTANCE' else 'RESISTANCE'
                opp=_first_touch(day,res,sup,touch_interval,start_after=exit1,target_type=opposite)
                if opp is not None:
                    t2=_trade(day,opp,cfg,2,'OPPOSITE_RECALC')
                    if t2 is not None:
                        t2['date']=str(sdate);t2.pop('_exit_ts',None);out.append(t2);day_pts+=t2['points'];count+=1
        daily.append({'date':str(sdate),'points':round(day_pts,2),'trades':count})
    summary=base._summary(out,stats);summary=dict(summary)
    summary['long_trades']=sum(t['side']=='LONG' for t in out);summary['short_trades']=sum(t['side']=='SHORT' for t in out);summary['opposite_recalc_trades']=sum(t.get('reference_phase')=='OPPOSITE_RECALC' for t in out)
    return summary,out,daily,base._monthly(out)
