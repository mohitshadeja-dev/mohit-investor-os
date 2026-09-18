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


def _filtered_result(trades, base_summary, details):
    allowed=[t for t in trades if details.get(str(t.get('date')),{}).get('status')=='ELIGIBLE'
             and t.get('side')==details.get(str(t.get('date')),{}).get('allowed_side')]
    stats={'engine':'sandbox_7576_intraday_first_candle_filter','test_days':base_summary.get('test_days',len(details)),
           'eligible_first_candle_days':sum(x['status']=='ELIGIBLE' for x in details.values()),
           'doji_days_skipped':sum(x['status']=='DOJI_SKIP' for x in details.values()),
           'wick_filter_days_skipped':sum(x['status']=='WICK_OVER_20_PERCENT_SKIP' for x in details.values()),
           'opposite_colour_trades_blocked':sum(1 for t in trades if details.get(str(t.get('date')),{}).get('status')=='ELIGIBLE' and t.get('side')!=details.get(str(t.get('date')),{}).get('allowed_side')),
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
    if mode == 'intraday':
        summary,trades,_,_=run_lab(df,cfg)
        return _filtered_result(trades,summary,first_candles)
    if mode != 'positional':
        raise ValueError("Execution mode must be 'intraday' or 'positional'")

    candidate_cfg = dict(cfg)
    candidate_cfg['execution_mode'] = 'intraday'
    _, candidates, _, _ = run_lab(df, candidate_cfg)
    minutes = norm(df)
    candidates = sorted((x for x in candidates if str(x.get('date')) in eligible
                         and x.get('side')==first_candles[str(x.get('date'))].get('allowed_side')),
                        key=lambda x: _ts(x['entry_time']))
    out = []
    blocked_until = None
    skipped_overlap = 0
    for candidate in candidates:
        entry_time = _ts(candidate['entry_time'])
        if blocked_until is not None and entry_time <= blocked_until:
            skipped_overlap += 1
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
