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


def _first_touch_time(day, level, kind):
    for _,m in day.iterrows():
        if kind=='RESISTANCE' and float(m.high)>=level:return m.date
        if kind=='SUPPORT' and float(m.low)<=level:return m.date
    return None


def _entry_hit(bar, side, trigger, mode):
    if mode in ('trigger','wick'):
        return float(bar.high)>=trigger if side=='LONG' else float(bar.low)<=trigger
    return float(bar.close)>=trigger if side=='LONG' else float(bar.close)<=trigger


def _stop_price(side,entry,buy,sell,cfg):
    sm=cfg.get('stop_mode','gann'); sv=float(cfg.get('stop_value',0) or 0)
    if sm in ('gann','gann_close_1m','gann_close_5m','gann_touch_entrybar'):
        return float(sell if side=='LONG' else buy)
    if sm=='points':return float(entry-sv if side=='LONG' else entry+sv)
    return float(entry*(1-sv/100) if side=='LONG' else entry*(1+sv/100))


def _target_price(side,entry,stop,cfg):
    tm=cfg.get('target_mode','points'); tv=float(cfg.get('target_value',100) or 100); risk=abs(entry-stop)
    if tm=='points':return float(entry+tv if side=='LONG' else entry-tv)
    if tm=='percent':return float(entry*(1+tv/100) if side=='LONG' else entry*(1-tv/100))
    return float(entry+risk*tv if side=='LONG' else entry-risk*tv)


def _exit_trade(day, entry_time, side, entry, stop, target, cfg):
    same=cfg.get('same_bar_policy','stop_first'); stop_mode=cfg.get('stop_mode','gann')
    trail_points=float(cfg.get('trail_to_cost_points',80) or 0)
    trail_active=False
    work=day[day.date>entry_time].reset_index(drop=True)
    ex=None;ext=None;reason=None
    for _,m in work.iterrows():
        # Stop execution follows the selected stop style. Gann-close variants use closes;
        # all other stop modes use intrabar price touch.
        if stop_mode=='gann_close_1m':
            hit_sl=float(m.close)<=stop if side=='LONG' else float(m.close)>=stop
        elif stop_mode=='gann_close_5m':
            # evaluated approximately on completed 5-minute boundaries
            mod=(int(m.date.hour)*60+int(m.date.minute)-(9*60+15))%5
            hit_sl=(mod==4) and (float(m.close)<=stop if side=='LONG' else float(m.close)>=stop)
        else:
            hit_sl=float(m.low)<=stop if side=='LONG' else float(m.high)>=stop
        if trail_active:
            hit_sl=False
        hit_t=float(m.high)>=target if side=='LONG' else float(m.low)<=target

        # The editable same-day strategy moves its stop to entry after an
        # 80-point favourable move.  For an OHLC bar that contains both the
        # activation level and entry, the agreed ordering is activation first,
        # followed by the cost-to-cost stop.
        activates_trail=(
            trail_points>0 and not trail_active and
            (float(m.high)>=entry+trail_points if side=='LONG' else float(m.low)<=entry-trail_points)
        )

        # Before the trail is armed, preserve the configured initial-stop
        # handling.  This also avoids pretending the favourable move happened
        # first when a single bar spans both the original stop and +80.
        if hit_sl and hit_t:
            if same=='exclude':return None,m.date,'AMBIGUOUS'
            if same=='target_first':ex=target;reason='TARGET'
            else:ex=stop;reason='SL'
            ext=m.date;break
        if hit_sl:ex=stop;ext=m.date;reason='SL';break

        if activates_trail:
            trail_active=True

        hit_cost=(
            trail_active and
            (float(m.low)<=entry if side=='LONG' else float(m.high)>=entry)
        )
        if hit_cost and hit_t:
            if same=='exclude':return None,m.date,'AMBIGUOUS'
            if same=='target_first':ex=target;reason='TARGET'
            else:ex=entry;reason='COST'
            ext=m.date;break
        if hit_cost:ex=entry;ext=m.date;reason='COST';break
        if hit_t:ex=target;ext=m.date;reason='TARGET';break
    if ex is None:
        last=day.iloc[-1];ex=float(last.close);ext=last.date;reason='EOD'
    return float(ex),ext,reason


def _confirm_reverse(day, after_time, side, trigger, confirm_interval):
    """SL reversal requires a completed confirmation candle beyond the opposite Gann boundary."""
    raw=day[day.date>after_time].reset_index(drop=True)
    if raw.empty:return None
    b=_bars(raw,confirm_interval)
    for _,bar in b.iterrows():
        c=float(bar.close)
        if side=='SHORT' and c<=trigger:return bar
        if side=='LONG' and c>=trigger:return bar
    return None


def run_same_day_sr(df,cfg):
    """Editable same-day Support/Resistance event engine.

    Structural rules:
      * Resistance WMA reference -> BUY ABOVE only.
      * Support WMA reference -> SELL BELOW only.
      * One open position at a time.
      * If a trade stops, the opposite Gann side may reverse after candle-close confirmation.
      * Both WMA references can activate independently during the same day.
      * After each exit, continue scanning chronologically for fresh valid signals until max trades/day.
    """
    d=base.norm(df);d['session']=d.date.dt.date
    sessions=[(k,v.drop(columns='session').reset_index(drop=True)) for k,v in d.groupby('session',sort=True)]
    out=[];daily=[]
    stats={'test_days':max(0,len(sessions)-1),'no_touch':0,'touch_ambiguous':0,'no_trigger':0,
           'same_bar_both':0,'large_red_first_candle':0,'large_green_first_candle':0,
           'engine':'same_day_sr_sequential'}
    wma=float(cfg.get('wma_factor',.382)); confirm=int(cfg.get('confirm_interval',1) or 1)
    sh,sm=map(int,str(cfg.get('start_time','09:15')).split(':'));eh,em=map(int,str(cfg.get('end_time','15:30')).split(':'))
    maxtr=cfg.get('max_trades_per_day');maxtr=int(maxtr) if maxtr not in (None,'',0) else 999
    reentry=bool(cfg.get('reentry',True)); direction=cfg.get('direction','both'); entry_mode=cfg.get('entry_mode','trigger')
    slip=float(cfg.get('slippage_points',0) or 0); cost=float(cfg.get('cost_points',0) or 0)

    for di in range(1,len(sessions)):
        sdate,day=sessions[di];_,prev=sessions[di-1]
        day=day[(day.date.dt.time>=time(sh,sm))&(day.date.dt.time<=time(eh,em))].reset_index(drop=True)
        if day.empty:continue
        ph=float(prev.high.max());pl=float(prev.low.min());pc=float(prev.iloc[-1].close)
        first5=day.iloc[:5]
        first_body=(float(first5.iloc[-1].close)-float(first5.iloc[0].open)) if len(first5)==5 else 0
        opening_gap=float(day.iloc[0].open)-pc
        block_long=opening_gap > 120 and first_body < 0
        block_short=opening_gap < -120 and first_body > 0
        if block_long:stats['large_red_first_candle']+=1
        if block_short:stats['large_green_first_candle']+=1
        res=pc+(ph-pl)*wma;sup=pc-(ph-pl)*wma
        refs={
            'RESISTANCE':{'level':float(res),'touch':_first_touch_time(day,float(res),'RESISTANCE'),'active':False,'armed':True},
            'SUPPORT':{'level':float(sup),'touch':_first_touch_time(day,float(sup),'SUPPORT'),'active':False,'armed':True},
        }
        if refs['RESISTANCE']['touch'] is None and refs['SUPPORT']['touch'] is None:
            stats['no_touch']+=1;continue

        grids={}
        for typ,r in refs.items():
            if r['touch'] is not None:
                buy,sell=base.gann(r['level'],float(cfg.get('gann_step',.125)))
                grids[typ]={'buy':float(buy),'sell':float(sell)}

        cursor=day.iloc[0].date
        count=0;day_pts=0
        pending_reverse=None

        while count<maxtr:
            # Activate WMA references once their touch time has occurred.
            for typ,r in refs.items():
                if r['touch'] is not None and r['touch']<=cursor:r['active']=True

            # SL reversal gets first priority because it belongs to the just-completed trade.
            if pending_reverse is not None:
                rr=pending_reverse
                cb=_confirm_reverse(day,cursor,rr['side'],rr['trigger'],confirm)
                if cb is not None:
                    side=rr['side']; typ=rr['ref_type']; g=grids[typ]
                    entry=float(cb.close)+(slip if side=='LONG' else -slip)
                    stop=_stop_price(side,entry,g['buy'],g['sell'],cfg)
                    target=_target_price(side,entry,stop,cfg)
                    ex,ext,reason=_exit_trade(day,cb.date,side,entry,stop,target,cfg)
                    if ex is not None:
                        count+=1;pts=((ex-entry) if side=='LONG' else (entry-ex))-cost
                        tr={'date':str(sdate),'trade_no':count,'reference_phase':'SL_REVERSAL','first_touch':typ,
                            'touch_time':str(refs[typ]['touch']),'reference_price':round(refs[typ]['level'],2),
                            'buy_above':g['buy'],'sell_below':g['sell'],'side':side,'entry_time':str(cb.date),
                            'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),'exit_time':str(ext),
                            'exit':round(float(ex),2),'reason':reason,'points':round(float(pts),2)}
                        out.append(tr);day_pts+=pts;cursor=ext+pd.Timedelta(seconds=1)
                        pending_reverse=None
                        # If the reversal itself stops, allow one new reversal only if re-entry is enabled.
                        if reentry and reason=='SL':
                            opp='SHORT' if side=='LONG' else 'LONG'
                            trig=g['sell'] if opp=='SHORT' else g['buy']
                            pending_reverse={'side':opp,'trigger':trig,'ref_type':typ}
                        if reason=='EOD':break
                        continue
                pending_reverse=None

            # Find the earliest next primary S/R signal after cursor.
            candidates=[]
            for typ,r in refs.items():
                if r['touch'] is None:continue
                if r['touch']>cursor:continue
                side='LONG' if typ=='RESISTANCE' else 'SHORT'
                # Gap-up >120 plus a red first 5m candle suppresses Resistance LONG;
                # gap-down >120 plus a green first 5m candle suppresses Support SHORT.
                # The opposite WMA reference remains live and can become trade #1.
                if block_long and side=='LONG':continue
                if block_short and side=='SHORT':continue
                if direction=='long' and side!='LONG':continue
                if direction=='short' and side!='SHORT':continue
                g=grids[typ];trigger=g['buy'] if side=='LONG' else g['sell']
                raw=day[day.date>=cursor].reset_index(drop=True)
                sig=_bars(raw,confirm)
                for _,bar in sig.iterrows():
                    # Fresh-cross re-arm for repeated primary entries on the same WMA grid.
                    if not r['armed']:
                        c=float(bar.close)
                        if side=='LONG' and c<trigger:r['armed']=True
                        if side=='SHORT' and c>trigger:r['armed']=True
                    if r['armed'] and _entry_hit(bar,side,trigger,entry_mode):
                        candidates.append((bar.date,typ,side,bar));break
            # A not-yet-active WMA reference may become available later in the day.
            future_touches=[r['touch'] for r in refs.values() if r['touch'] is not None and r['touch']>cursor]
            if not candidates:
                if future_touches:
                    cursor=min(future_touches)
                    continue
                break

            candidates.sort(key=lambda x:x[0]); etime,typ,side,eb=candidates[0];g=grids[typ]
            if entry_mode=='trigger':entry=float(g['buy'] if side=='LONG' else g['sell'])
            else:entry=float(eb.close)+(slip if side=='LONG' else -slip)
            stop=_stop_price(side,entry,g['buy'],g['sell'],cfg);target=_target_price(side,entry,stop,cfg)
            ex,ext,reason=_exit_trade(day,etime,side,entry,stop,target,cfg)
            refs[typ]['armed']=False
            if ex is None:
                cursor=ext+pd.Timedelta(seconds=1);continue
            count+=1;pts=((ex-entry) if side=='LONG' else (entry-ex))-cost
            phase='RESISTANCE_GRID' if typ=='RESISTANCE' else 'SUPPORT_GRID'
            tr={'date':str(sdate),'trade_no':count,'reference_phase':phase,'first_touch':typ,
                'touch_time':str(refs[typ]['touch']),'reference_price':round(refs[typ]['level'],2),
                'buy_above':g['buy'],'sell_below':g['sell'],'side':side,'entry_time':str(etime),
                'entry':round(entry,2),'stop':round(stop,2),'target':round(target,2),'exit_time':str(ext),
                'exit':round(float(ex),2),'reason':reason,'points':round(float(pts),2)}
            out.append(tr);day_pts+=pts;cursor=ext+pd.Timedelta(seconds=1)
            if reentry and reason=='SL':
                opp='SHORT' if side=='LONG' else 'LONG'
                trig=g['sell'] if opp=='SHORT' else g['buy']
                pending_reverse={'side':opp,'trigger':trig,'ref_type':typ}
            if reason=='EOD':break

        daily.append({'date':str(sdate),'points':round(float(day_pts),2),'trades':count})

    summary=base._summary(out,stats)
    summary['long_trades']=sum(t['side']=='LONG' for t in out)
    summary['short_trades']=sum(t['side']=='SHORT' for t in out)
    summary['resistance_grid_trades']=sum(t.get('reference_phase')=='RESISTANCE_GRID' for t in out)
    summary['support_grid_trades']=sum(t.get('reference_phase')=='SUPPORT_GRID' for t in out)
    summary['sl_reversal_trades']=sum(t.get('reference_phase')=='SL_REVERSAL' for t in out)
    summary['cost_to_cost_exits']=sum(t.get('reason')=='COST' for t in out)
    return summary,out,daily,base._monthly(out)


def run_lab(df,cfg):
    if cfg.get('touch_side')=='both_recalc':return run_same_day_sr(df,cfg)
    return legacy.run_lab(df,cfg)
