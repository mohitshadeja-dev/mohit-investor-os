from __future__ import annotations
from datetime import time
import pandas as pd
from . import strategy_lab_base as base

def _bars(day,minutes=5):
    x=day.set_index('date')
    return x.resample(f'{minutes}min',origin='start_day',offset='15min').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last')).dropna().reset_index()

def _first_touch(day,resistance,support,start_after=None,target_type=None):
    work=day if start_after is None else day[day.date>start_after].reset_index(drop=True)
    if work.empty:return None
    for _,bar in _bars(work).iterrows():
        hit_res=float(bar.high)>=resistance and target_type!='SUPPORT';hit_sup=float(bar.low)<=support and target_type!='RESISTANCE'
        if hit_res and hit_sup:return {'ambiguous':True,'time':bar.date}
        if hit_res or hit_sup:
            typ='RESISTANCE' if hit_res else 'SUPPORT';level=float(resistance if hit_res else support);touched=bar.date
            raw=work[(work.date>=bar.date)&(work.date<bar.date+pd.Timedelta(minutes=5))]
            for _,minute in raw.iterrows():
                if (typ=='RESISTANCE' and float(minute.high)>=level) or (typ=='SUPPORT' and float(minute.low)<=level):touched=minute.date;break
            return {'ambiguous':False,'type':typ,'level':level,'time':touched}
    return None

def _trade_state(day,ref,trade_no,phase):
    side='LONG' if ref['type']=='RESISTANCE' else 'SHORT';buy,sell=base.gann(ref['level'],.125);trigger=buy if side=='LONG' else sell;entry_bar=None
    for _,bar in day[day.date>=ref['time']].iterrows():
        if (side=='LONG' and float(bar.high)>=trigger) or (side=='SHORT' and float(bar.low)<=trigger):entry_bar=bar;break
    grid={'reference_phase':phase,'first_touch':ref['type'],'reference_price':round(ref['level'],2),'buy_above':buy,'sell_below':sell,'side':side}
    if entry_bar is None:return None,grid
    entry=float(trigger);stop=entry-150 if side=='LONG' else entry+150;target=entry+150 if side=='LONG' else entry-150
    trade={'date':str(entry_bar.date.date()),'trade_no':trade_no,**grid,'touch_time':str(ref['time']),'entry_time':str(entry_bar.date),'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),'exit_time':None,'exit':None,'reason':None,'points':None,'status':'OPEN','_exit_ts':None}
    for _,minute in day[day.date>entry_bar.date].iterrows():
        hit_sl=float(minute.low)<=stop if side=='LONG' else float(minute.high)>=stop;hit_target=float(minute.high)>=target if side=='LONG' else float(minute.low)<=target
        if hit_sl or hit_target:
            reason='SL' if hit_sl else 'TARGET';price=stop if hit_sl else target
            trade.update({'exit_time':str(minute.date),'exit':round(price,2),'reason':reason,'points':-150.0 if reason=='SL' else 150.0,'status':'CLOSED','_exit_ts':minute.date});break
    return trade,grid

def scan_live_7575(df):
    """Live counterpart of the locked 7,575.6 Same-Day Both S/R engine."""
    d=base.norm(df);d['session']=d.date.dt.date;sessions=[(k,v.drop(columns='session').reset_index(drop=True)) for k,v in d.groupby('session',sort=True)]
    if len(sessions)<2:return {'state':'WAITING_DATA','message':'Need previous and current session data','current_trade':None}
    session_date,day=sessions[-1];_,prev=sessions[-2];day=day[(day.date.dt.time>=time(9,15))&(day.date.dt.time<=time(15,30))].reset_index(drop=True)
    if day.empty:return {'date':str(session_date),'state':'WAITING_DATA','message':'No current-session candles','current_trade':None}
    ph=float(prev.high.max());pl=float(prev.low.min());pc=float(prev.iloc[-1].close);wma=(ph-pl)*.382;resistance=pc+wma;support=pc-wma
    out={'date':str(session_date),'engine':'locked_7575_live','prev_high':round(ph,2),'prev_low':round(pl,2),'prev_close':round(pc,2),'wma':round(wma,2),'resistance':round(resistance,2),'support':round(support,2),'latest_close':round(float(day.iloc[-1].close),2),'latest_time':str(day.iloc[-1].date),'current_trade':None,'latest_trade':None}
    first=_first_touch(day,resistance,support)
    if first is None:return {**out,'state':'WAITING_TOUCH','message':'Waiting for first 5-minute Support/Resistance touch'}
    if first.get('ambiguous'):return {**out,'state':'AMBIGUOUS_TOUCH','message':'Support and Resistance touched in the same 5-minute candle'}
    trade1,grid1=_trade_state(day,first,1,'FIRST_WMA');out.update(grid1)
    if trade1 is None:return {**out,'state':'WAITING_TRIGGER','message':f'{first["type"]} touched; waiting for its Gann trigger'}
    clean1={k:v for k,v in trade1.items() if not k.startswith('_')};out['latest_trade']=clean1
    if trade1['status']=='OPEN':return {**out,'state':'TRADE_OPEN','message':'EXECUTION READY — confirmed locked-baseline signal','current_trade':clean1}
    opposite='SUPPORT' if first['type']=='RESISTANCE' else 'RESISTANCE';second=_first_touch(day,resistance,support,start_after=trade1['_exit_ts'],target_type=opposite)
    if second is None:return {**out,'state':'WAITING_OPPOSITE','message':f'Trade 1 closed; waiting for {opposite} recalculation'}
    trade2,grid2=_trade_state(day,second,2,'OPPOSITE_RECALC');out.update(grid2)
    if trade2 is None:return {**out,'state':'WAITING_TRIGGER','message':f'{opposite} touched; waiting for its Gann trigger'}
    clean2={k:v for k,v in trade2.items() if not k.startswith('_')};out['latest_trade']=clean2
    if trade2['status']=='OPEN':return {**out,'state':'TRADE_OPEN','message':'EXECUTION READY — confirmed opposite-level signal','current_trade':clean2}
    return {**out,'state':'DAY_COMPLETE','message':'Both Same-Day S/R opportunities are complete'}
