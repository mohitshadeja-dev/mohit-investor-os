from __future__ import annotations

import pandas as pd

from .strategy_lab import run_lab
from .strategy_lab_base import _monthly, _summary, norm


def _ts(value):
    return pd.Timestamp(value)


def _first_candle_filter(df, cfg):
    d = norm(df); d['session'] = d.date.dt.date
    minimum = float(cfg.get('first_candle_body_percent', 80) or 80) / 100.0
    doji_max = float(cfg.get('doji_body_percent', 10) or 10) / 100.0
    eligible, details = set(), {}
    for session, day in d.groupby('session', sort=True):
        first = day.iloc[:5]
        if len(first) < 5:
            details[str(session)] = {'status': 'INCOMPLETE_FIRST_5M'}; continue
        open_, close = float(first.iloc[0].open), float(first.iloc[-1].close)
        high, low = float(first.high.max()), float(first.low.min())
        candle_range = high - low; body = abs(close - open_)
        ratio = body / candle_range if candle_range > 0 else 0.0
        if candle_range <= 0 or ratio <= doji_max: status = 'DOJI_SKIP'
        elif ratio < minimum: status = 'WICK_OVER_20_PERCENT_SKIP'
        else: status = 'ELIGIBLE'; eligible.add(str(session))
        allowed_side = 'LONG' if close > open_ else ('SHORT' if close < open_ else None)
        details[str(session)] = {'status': status, 'allowed_side': allowed_side,
                                 'candle_colour': 'GREEN' if allowed_side=='LONG' else ('RED' if allowed_side=='SHORT' else 'DOJI'),
                                 'body_percent': round(ratio*100,2), 'wick_percent': round((1-ratio)*100,2) if candle_range>0 else 100.0}
    return eligible, details


def _previous_day_levels(df, cfg):
    d=norm(df);d['session']=d.date.dt.date
    sessions=[(k,v) for k,v in d.groupby('session',sort=True)]
    ratio=float(cfg.get('fibonacci_ratio',.382) or .382)
    levels={}
    for i in range(1,len(sessions)):
        current,_=sessions[i];_,prev=sessions[i-1]
        high,low=float(prev.high.max()),float(prev.low.min());span=high-low
        levels[str(current)]={'prev_high':high,'prev_low':low,
                              'low_to_high':low+span*ratio,'high_to_low':high-span*ratio}
    return levels


def _period_key(trade, period):
    d=pd.Timestamp(str(trade.get('date')))
    if period=='week':
        iso=d.isocalendar();return f'{int(iso.year)}-W{int(iso.week):02d}'
    if period=='month':return d.strftime('%Y-%m')
    return d.strftime('%Y-%m-%d')


def _apply_research_filters(trades, details, levels, cfg, apply_period=True):
    direction=str(cfg.get('direction','both')).lower();fib=str(cfg.get('fibonacci_mode','off')).lower()
    include_tuesday=bool(cfg.get('include_tuesday',True));period=str(cfg.get('one_trade_period','day')).lower()
    maximum=max(1,int(cfg.get('max_trades_period',3) or 3))
    allowed=[];counts={};blocked={'colour':0,'tuesday':0,'fibonacci':0,'period':0}
    for t in sorted(trades,key=lambda x:_ts(x['entry_time'])):
        date=str(t.get('date'));info=details.get(date,{})
        if info.get('status')!='ELIGIBLE' or t.get('side')!=info.get('allowed_side'):
            blocked['colour']+=1;continue
        if direction=='long' and t.get('side')!='LONG':continue
        if direction=='short' and t.get('side')!='SHORT':continue
        if not include_tuesday and pd.Timestamp(date).weekday()==1:
            blocked['tuesday']+=1;continue
        if fib!='off':
            lv=levels.get(date);entry=float(t.get('entry',0));side=t.get('side')
            threshold=lv.get(fib) if lv else None
            if threshold is None or (side=='LONG' and entry<=threshold) or (side=='SHORT' and entry>=threshold):
                blocked['fibonacci']+=1;continue
            t={**t,'previous_day_fibonacci':round(float(threshold),2),'fibonacci_mode':fib}
        if apply_period:
            key=_period_key(t,period)
            if counts.get(key,0)>=maximum:
                blocked['period']+=1;continue
            counts[key]=counts.get(key,0)+1
        allowed.append(t)
    return allowed,blocked


def _filtered_result(trades, base_summary, details, levels, cfg):
    allowed,blocked=_apply_research_filters(trades,details,levels,cfg)
    stats={'engine':'sandbox_7576_intraday_first_candle_filter','test_days':base_summary.get('test_days',len(details)),
           'eligible_first_candle_days':sum(x['status']=='ELIGIBLE' for x in details.values()),
           'doji_days_skipped':sum(x['status']=='DOJI_SKIP' for x in details.values()),
           'wick_filter_days_skipped':sum(x['status']=='WICK_OVER_20_PERCENT_SKIP' for x in details.values()),
           'opposite_colour_trades_blocked':blocked['colour'],'tuesday_trades_blocked':blocked['tuesday'],
           'fibonacci_trades_blocked':blocked['fibonacci'],'period_limit_trades_blocked':blocked['period'],
           'no_touch':base_summary.get('no_touch',0),'touch_ambiguous':base_summary.get('touch_ambiguous',0),
           'no_trigger':base_summary.get('no_trigger',0),'same_bar_both':base_summary.get('same_bar_both',0)}
    summary=_summary(allowed,stats);summary['cost_to_cost_exits']=sum(t.get('reason')=='COST' for t in allowed)
    daily_map={}
    for t in allowed:
        r=daily_map.setdefault(str(t['date']),{'date':str(t['date']),'points':0.0,'trades':0});r['points']+=float(t['points']);r['trades']+=1
    daily=[{**v,'points':round(v['points'],2)} for _,v in sorted(daily_map.items())]
    return summary,allowed,daily,_monthly(allowed)


def _positional_exit(minutes, trade, cfg):
    side = trade['side']
    entry = float(trade['entry'])
    stop = float(trade['stop'])
    target = float(trade['target'])
    original_stop = stop
    same = cfg.get('same_bar_policy', 'stop_first')
    trail = float(cfg.get('trail_to_cost_points', 0) or 0)
    cost = float(cfg.get('cost_points', 0) or 0)
    entry_time = _ts(trade['entry_time'])
    future = minutes[minutes.date > entry_time]
    if future.empty:
        return entry, entry_time, 'DATA_END', stop

    last = future.iloc[-1]
    exit_price, exit_time, reason = float(last.close), last.date, 'DATA_END'
    armed_cost = False
    for _, bar in future.iterrows():
        high, low = float(bar.high), float(bar.low)
        if trail > 0 and not armed_cost:
            moved = high >= entry + trail if side == 'LONG' else low <= entry - trail
            if moved:
                armed_cost = True
                stop = entry

        hit_stop = low <= stop if side == 'LONG' else high >= stop
        hit_target = high >= target if side == 'LONG' else low <= target
        if hit_stop and hit_target:
            if same == 'exclude':
                continue
            if same == 'target_first':
                exit_price, reason = target, 'TARGET'
            else:
                exit_price, reason = stop, 'COST' if armed_cost and stop == entry else 'SL'
            exit_time = bar.date
            break
        if hit_stop:
            exit_price, exit_time = stop, bar.date
            reason = 'COST' if armed_cost and stop == entry else 'SL'
            break
        if hit_target:
            exit_price, exit_time, reason = target, bar.date, 'TARGET'
            break

    points = (exit_price - entry) if side == 'LONG' else (entry - exit_price)
    return exit_price, exit_time, reason, original_stop, points - cost


def run_7576_sandbox(df, cfg):
    """Independent research copy of the 7,575.6 setup.

    Intraday delegates to the existing editable research engine. Positional mode
    uses those independently generated entry candidates, freezes the entry-day
    levels and carries one position across sessions until target/SL/data end.
    It never changes or calls the locked audit preset.
    """
    mode = str(cfg.get('execution_mode', 'intraday')).lower()
    eligible, first_candles = _first_candle_filter(df, cfg)
    previous_levels = _previous_day_levels(df, cfg)
    if mode == 'intraday':
        summary,trades,_,_=run_lab(df,cfg)
        return _filtered_result(trades,summary,first_candles,previous_levels,cfg)
    if mode != 'positional':
        raise ValueError("Execution mode must be 'intraday' or 'positional'")

    candidate_cfg = dict(cfg)
    candidate_cfg['execution_mode'] = 'intraday'
    _, candidates, _, _ = run_lab(df, candidate_cfg)
    minutes = norm(df)
    candidates,blocked_filters=_apply_research_filters(candidates,first_candles,previous_levels,cfg,apply_period=False)
    out = []
    blocked_until = None
    skipped_overlap = 0
    period_counts = {}
    period = str(cfg.get('one_trade_period','day')).lower()
    maximum = max(1,int(cfg.get('max_trades_period',3) or 3))
    for candidate in candidates:
        entry_time = _ts(candidate['entry_time'])
        if blocked_until is not None and entry_time <= blocked_until:
            skipped_overlap += 1
            continue
        period_key = _period_key(candidate, period)
        if period_counts.get(period_key,0) >= maximum:
            blocked_filters['period'] += 1
            continue
        result = _positional_exit(minutes, candidate, cfg)
        if len(result) == 4:
            exit_price, exit_time, reason, frozen_stop = result
            points = 0.0
        else:
            exit_price, exit_time, reason, frozen_stop, points = result
        trade = dict(candidate)
        trade.update({
            'stop': round(float(frozen_stop), 2),
            'exit_time': str(exit_time),
            'exit': round(float(exit_price), 2),
            'reason': reason,
            'points': round(float(points), 2),
            'execution_mode': 'POSITIONAL',
            'carried_overnight': _ts(exit_time).date() > entry_time.date(),
        })
        out.append(trade)
        period_counts[period_key] = period_counts.get(period_key,0) + 1
        blocked_until = _ts(exit_time)

    stats = {
        'engine': 'sandbox_7576_positional',
        'test_days': len({str(x.date()) for x in minutes.date}),
        'candidate_entries': len(candidates),
        'skipped_overlapping_entries': skipped_overlap,
        'overnight_carries': sum(bool(t['carried_overnight']) for t in out),
        'eligible_first_candle_days': len(eligible),
        'doji_days_skipped': sum(x['status']=='DOJI_SKIP' for x in first_candles.values()),
        'wick_filter_days_skipped': sum(x['status']=='WICK_OVER_20_PERCENT_SKIP' for x in first_candles.values()),
        'opposite_colour_trades_blocked':blocked_filters['colour'],
        'tuesday_trades_blocked':blocked_filters['tuesday'],
        'fibonacci_trades_blocked':blocked_filters['fibonacci'],
        'period_limit_trades_blocked':blocked_filters['period'],
        'no_touch': 0, 'touch_ambiguous': 0, 'no_trigger': 0, 'same_bar_both': 0,
    }
    summary = _summary(out, stats)
    daily_map = {}
    for trade in out:
        key = str(trade['date'])
        row = daily_map.setdefault(key, {'date': key, 'points': 0.0, 'trades': 0})
        row['points'] += float(trade['points'])
        row['trades'] += 1
    daily = [{**v, 'points': round(v['points'], 2)} for _, v in sorted(daily_map.items())]
    return summary, out, daily, _monthly(out)
