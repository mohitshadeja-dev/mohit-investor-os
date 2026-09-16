from __future__ import annotations
from datetime import time
import pandas as pd
from . import strategy_lab_base as base
from . import strategy_lab_patched_impl as legacy


def _bars(df, minutes):
    minutes=max(1,int(minutes or 1))
    if minutes==1:return df.reset_index(drop=True)
    x=df.set_index('date')
    return x.resample(f'{minutes}min',origin='start_day',offset='15min').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last')).dropna().reset_index()


def _first_wma_touch(day,res,sup,minutes,start_after=None,target_type=None):
    work=day if start_after is None else day[day.date>start_after].reset_index(drop=True)
    if work.empty:return None
    if target_type in ('RESISTANCE','SUPPORT'):
        level=res if target_type=='RESISTANCE' else sup
        for _,m in work.iterrows():
            if target_type=='RESISTANCE' and float(m.high)>=level:return {'type':'RESISTANCE','level':float(level),'time':m.date}
            if target_type=='SUPPORT' and float(m.low)<=level:return {'type':'SUPPORT','level':float(level),'time':m.date}
        return None
    b=_bars(work,minutes)
    for _,r in b.iterrows():
        hr=float(r.high)>=res; hs=float(r.low)<=sup
        if hr and hs:return {'ambiguous':True,'time':r.date}
        if hr or hs:
            typ='RESISTANCE' if hr else 'SUPPORT'; level=float(res if hr else sup)
            end=r.date+pd.Timedelta(minutes=max(1,int(minutes)))
            raw=work[(work.date>=r.date)&(work.date<end)]
            tt=r.date
            for _,m in raw.iterrows():
                if (typ=='RESISTANCE' and float(m.high)>=level) or (typ=='SUPPORT' and float(m.low)<=level):tt=m.date;break
            return {'ambiguous':False,'type':typ,'level':level,'time':tt}
    return None


def _entry_signal(bar,side,buy,sell,mode):
    if mode in ('trigger','wick'):
        return float(bar.high)>=buy if side=='LONG' else float(bar.low)<=sell
    return float(bar.close)>=buy if side=='LONG' else float(bar.close)<=sell


def _stop_price(side,entry,buy,sell,cfg):
    sm=cfg.get('stop_mode','gann'); sv=float(cfg.get('stop_value',0) or 0)
    if sm=='gann':return float(sell if side=='LONG' else buy)
    if sm=='points':return float(entry-sv if side=='LONG' else entry+sv)
    return float(entry*(1-sv/100) if side=='LONG' else entry*(1+sv/100))


def _target_price(side,entry,stop,cfg):
    tm=cfg.get('target_mode','points'); tv=float(cfg.get('target_value',100) or 100); risk=abs(entry-stop)
    if tm=='points':return float(entry+tv if side=='LONG' else entry-tv)
    if tm=='percent':return float(entry*(1+tv/100) if side=='LONG' else entry*(1-tv/100))
    return float(entry+risk*tv if side=='LONG' else entry-risk*tv)


def _trade(day,start_time,ref_type,ref_price,trade_no,phase,cfg):
    gstep=float(cfg.get('gann_step',.125)); buy,sell=base.gann(ref_price,gstep)
    confirm=int(cfg.get('confirm_interval',1) or 1); entry_mode=cfg.get('entry_mode','trigger'); direction=cfg.get('direction','both')
    slip=float(cfg.get('slippage_points',0) or 0); cost=float(cfg.get('cost_points',0) or 0); same=cfg.get('same_bar_policy','stop_first')

    # Fixed structural mapping for this strategy:
    # Resistance reference -> BUY ABOVE only.
    # Support reference -> SELL BELOW only.
    side='LONG' if ref_type=='RESISTANCE' else 'SHORT'
    if direction=='long' and side!='LONG':return None
    if direction=='short' and side!='SHORT':return None

    sig=_bars(day[day.date>=start_time].reset_index(drop=True),confirm)
    entrybar=None
    for _,b in sig.iterrows():
        if _entry_signal(b,side,buy,sell,entry_mode):
            entrybar=b;break
    if entrybar is None:return None

    if entry_mode=='trigger':entry=float(buy if side=='LONG' else sell)
    else:entry=float(entrybar.close)+(slip if side=='LONG' else -slip)
    stop=_stop_price(side,entry,buy,sell,cfg); target=_target_price(side,entry,stop,cfg)
    ex=None; ext=None; reason=None
    minute=day[day.date>entrybar.date].reset_index(drop=True)
    for _,m in minute.iterrows():
        hit_sl=float(m.low)<=stop if side=='LONG' else float(m.high)>=stop
        hit_t=float(m.high)>=target if side=='LONG' else float(m.low)<=target
        if hit_sl and hit_t:
            if same=='exclude':return {'ambiguous':True,'_exit_ts':m.date}
            if same=='target_first':ex=target;reason='TARGET'
            else:ex=stop;reason='SL'
            ext=m.date;break
        if hit_sl:ex=stop;ext=m.date;reason='SL';break
        if hit_t:ex=target;ext=m.date;reason='TARGET';break
    if ex is None:
        last=day.iloc[-1];ex=float(last.close);ext=last.date;reason='EOD'
    pts=((ex-entry) if side=='LONG' else (entry-ex))-cost
    return {'trade_no':trade_no,'reference_phase':phase,'first_touch':ref_type,'touch_time':str(start_time),'reference_price':round(float(ref_price),2),'buy_above':buy,'sell_below':sell,'side':side,'entry_time':str(entrybar.date),'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),'exit_time':str(ext),'exit':round(float(ex),2),'reason':reason,'points':round(float(pts),2),'_exit_ts':ext}


def run_same_day_sr(df,cfg):
    d=base.norm(df);d['session']=d.date.dt.date
    sessions=[(k,v.drop(columns='session').reset_index(drop=True)) for k,v in d.groupby('session',sort=True)]
    out=[];daily=[];stats={'test_days':max(0,len(sessions)-1),'no_touch':0,'touch_ambiguous':0,'no_trigger':0,'same_bar_both':0,'engine':'same_day_sr_editable'}
    wma=float(cfg.get('wma_factor',.382));touch_int=int(cfg.get('touch_interval',5) or 5)
    sh,sm=map(int,str(cfg.get('start_time','09:15')).split(':'));eh,em=map(int,str(cfg.get('end_time','15:30')).split(':'))
    maxtr=cfg.get('max_trades_per_day');maxtr=int(maxtr) if maxtr not in (None,'',0) else 2
    reentry=bool(cfg.get('reentry',True))
    for di in range(1,len(sessions)):
        sdate,day=sessions[di];_,prev=sessions[di-1]
        day=day[(day.date.dt.time>=time(sh,sm))&(day.date.dt.time<=time(eh,em))].reset_index(drop=True)
        if day.empty:continue
        ph=float(prev.high.max());pl=float(prev.low.min());pc=float(prev.iloc[-1].close);res=pc+(ph-pl)*wma;sup=pc-(ph-pl)*wma
        first=_first_wma_touch(day,res,sup,touch_int)
        if first is None:stats['no_touch']+=1;continue
        if first.get('ambiguous'):stats['touch_ambiguous']+=1;continue
        day_pts=0;count=0
        t1=_trade(day,first['time'],first['type'],first['level'],1,'FIRST_WMA',cfg)
        if t1 is None or t1.get('ambiguous'):
            stats['no_trigger']+=1;continue
        exit1=t1.pop('_exit_ts');t1['date']=str(sdate);out.append(t1);day_pts+=t1['points'];count+=1
        if reentry and count<maxtr and t1['reason']!='EOD':
            opposite='SUPPORT' if first['type']=='RESISTANCE' else 'RESISTANCE'
            opp=_first_wma_touch(day,res,sup,touch_int,start_after=exit1,target_type=opposite)
            if opp is not None:
                t2=_trade(day,opp['time'],opp['type'],opp['level'],2,'OPPOSITE_RECALC',cfg)
                if t2 is not None and not t2.get('ambiguous'):
                    t2.pop('_exit_ts',None);t2['date']=str(sdate);out.append(t2);day_pts+=t2['points'];count+=1
        daily.append({'date':str(sdate),'points':round(float(day_pts),2),'trades':count})
    summary=base._summary(out,stats);summary['long_trades']=sum(t['side']=='LONG' for t in out);summary['short_trades']=sum(t['side']=='SHORT' for t in out);summary['opposite_recalc_trades']=sum(t.get('reference_phase')=='OPPOSITE_RECALC' for t in out)
    return summary,out,daily,base._monthly(out)


def run_lab(df,cfg):
    if cfg.get('touch_side')=='both_recalc':return run_same_day_sr(df,cfg)
    return legacy.run_lab(df,cfg)
