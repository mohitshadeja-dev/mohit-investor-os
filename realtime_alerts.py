from __future__ import annotations

import csv
import io
import math
import os
import threading
import time as pytime
import urllib.request
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException

from .kite_service import client, fetch_minutes


IST = ZoneInfo("Asia/Kolkata")
router = APIRouter(prefix="/api/live/alerts", tags=["live-alerts"])
NIFTY_TOKEN = int(os.getenv("NIFTY_INSTRUMENT_TOKEN", "256265"))

_LOCK = threading.Lock()
_STOCK_CACHE = {
    "status": "IDLE",
    "started_at": None,
    "updated_at": None,
    "scanned": 0,
    "total": 100,
    "qualified": [],
    "candidates": [],
    "errors": [],
}


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    if getattr(d["date"].dt, "tz", None) is None:
        d["date"] = d["date"].dt.tz_localize(IST)
    else:
        d["date"] = d["date"].dt.tz_convert(IST)
    return d.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def _bars(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    x = df.set_index("date").sort_index()
    return x.resample(
        f"{minutes}min", origin="start_day", offset="9h15min",
        label="left", closed="left",
    ).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    ).dropna().reset_index()


def _rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    x = df.copy()
    delta = x["close"].astype(float).diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    x["rsi14"] = 100 - 100 / (1 + rs)
    return x


def _levels(prev: pd.DataFrame) -> dict:
    high = float(prev.high.max())
    low = float(prev.low.min())
    close = float(prev.iloc[-1].close)
    span = high - low
    return {
        "high": high,
        "low": low,
        "close": close,
        "resistance": close + span * 0.382,
        "support": close - span * 0.382,
        "midpoint": low + span * 0.5,
        "fib_upper_382": high - span * 0.382,
        "fib_lower_382": low + span * 0.382,
    }


def _gann(price: float, step: float = 0.125) -> tuple[float, float]:
    root = math.sqrt(price)
    base = math.floor(root / step) * step
    return round((base + step) ** 2, 2), round(base ** 2, 2)


def _split_window(ts) -> bool:
    t = ts.time()
    return time(9, 18) <= t <= time(10, 59) or time(14, 0) <= t <= time(14, 30)


def _sessions(raw: pd.DataFrame):
    d = _normalise(raw)
    d = d[(d.date.dt.time >= time(9, 15)) & (d.date.dt.time <= time(15, 29))]
    d = _rsi(d)
    d["session"] = d.date.dt.date
    return d, [(k, v.drop(columns="session").reset_index(drop=True)) for k, v in d.groupby("session", sort=True)]


def scan_nifty39(raw: pd.DataFrame) -> dict:
    """Exact live entry gate for the audited 39-trade NIFTY setup."""
    d, sessions = _sessions(raw)
    if len(sessions) < 2:
        return {"state": "WAITING_DATA", "message": "Need previous and current sessions"}
    session_date, day = sessions[-1]
    _, prev = sessions[-2]
    levels = _levels(prev)
    b5 = _bars(day, 5)
    if b5.empty:
        return {"state": "WAITING_DATA", "message": "No current-session candles"}

    ref = None
    for _, bar in b5.iterrows():
        hit_r = float(bar.high) >= levels["resistance"]
        hit_s = float(bar.low) <= levels["support"]
        if hit_r and hit_s:
            return {"state": "AMBIGUOUS_WMA", "message": "Both WMA levels touched in one 5-minute candle"}
        if hit_r or hit_s:
            side = "LONG" if hit_r else "SHORT"
            ref_price = levels["resistance"] if hit_r else levels["support"]
            raw5 = day[(day.date >= bar.date) & (day.date < bar.date + pd.Timedelta(minutes=5))]
            touch_time = bar.date
            for _, minute in raw5.iterrows():
                if (side == "LONG" and float(minute.high) >= ref_price) or (side == "SHORT" and float(minute.low) <= ref_price):
                    touch_time = minute.date
                    break
            ref = (side, ref_price, touch_time)
            break

    base = {
        "strategy": "NIFTY_39",
        "date": str(session_date),
        "latest_close": round(float(day.iloc[-1].close), 2),
        "latest_time": str(day.iloc[-1].date),
        "retests_allowed": True,
        **{k: round(v, 2) for k, v in levels.items()},
    }
    if ref is None:
        return {**base, "state": "WAITING_WMA", "message": "Waiting for first WMA Support/Resistance touch"}

    side, ref_price, touch_time = ref
    buy, sell = _gann(ref_price)
    trigger = buy if side == "LONG" else sell
    first5 = b5.iloc[0]
    opening_aligned = float(first5.close) > float(first5.open) if side == "LONG" else float(first5.close) < float(first5.open)
    gap = float(first5.open) - levels["close"]
    body_pct = abs(float(first5.close) - float(first5.open)) / max(float(first5.high) - float(first5.low), 1e-9) * 100
    gap_exception = (
        side == "LONG" and gap >= 100 and float(first5.close) < float(first5.open) and body_pct >= 60
    ) or (
        side == "SHORT" and gap <= -100 and float(first5.close) > float(first5.open) and body_pct >= 60
    )
    gate = {
        "opening_direction": opening_aligned,
        "gap_body_exception_clear": not gap_exception,
    }
    common = {
        **base,
        "side": side,
        "reference_price": round(ref_price, 2),
        "wma_touch_time": str(touch_time),
        "gann_trigger": trigger,
        "buy_above": buy,
        "sell_below": sell,
        "opening_body_pct": round(body_pct, 2),
        "opening_gap_points": round(gap, 2),
        "checks": gate,
    }
    if not opening_aligned or gap_exception:
        failed = [k for k, ok in gate.items() if not ok]
        return {**common, "state": "OPENING_FILTER_REJECTED", "failed": failed, "message": "Opening filter rejected"}

    for pos, row in day[day.date >= touch_time].iterrows():
        if pos <= 0 or not _split_window(row.date):
            continue
        actual = float(row.low) <= trigger <= float(row.high)
        if not actual:
            continue
        prior = day.iloc[pos - 1]
        rsi1 = float(prior.rsi14)
        fib_ok = trigger > levels["midpoint"] and trigger > levels["fib_upper_382"] if side == "LONG" else trigger < levels["midpoint"] and trigger < levels["fib_lower_382"]
        rsi_ok = rsi1 > 68 if side == "LONG" else rsi1 < 32
        checks = {**gate, "entry_window": True, "actual_gann_touch": True, "fibonacci": bool(fib_ok), "rsi_1m": bool(rsi_ok)}
        if fib_ok and rsi_ok:
            return {
                **common,
                "state": "ALERT",
                "message": f"ENTER {side} at {trigger:.2f}",
                "entry_time": str(row.date),
                "entry": trigger,
                "target": round(trigger + 200 if side == "LONG" else trigger - 200, 2),
                "stop": round(trigger - 200 if side == "LONG" else trigger + 200, 2),
                "rsi1": round(rsi1, 2),
                "checks": checks,
                "signal_key": f"NIFTY39|{session_date}|{side}|{trigger}|{row.date}",
                "exit_rule": "Dynamic 3-minute Fib 0.618 after +50 points; otherwise ±200 or EOD",
            }
    return {**common, "state": "WAITING_ENTRY", "message": "WMA grid ready; waiting for a qualifying Gann touch"}


def scan_stock_aplus(symbol: str, raw: pd.DataFrame, target_day: date) -> dict:
    """Locked Stock A+ live gate (11/11 research version)."""
    d, sessions = _sessions(raw)
    ti = next((i for i, (dt, _) in enumerate(sessions) if dt == target_day), None)
    if ti is None or ti < 21:
        return {"symbol": symbol, "status": "NO_DATA"}
    day = sessions[ti][1]
    prev = sessions[ti - 1][1]
    levels = _levels(prev)
    all_b5 = _rsi(_bars(d, 5))
    b5 = all_b5[all_b5.date.dt.date == target_day].reset_index(drop=True)
    if b5.empty:
        return {"symbol": symbol, "status": "NO_FIRST5"}
    f = b5.iloc[0]
    op, hi, lo, cl, vol = map(float, [f.open, f.high, f.low, f.close, f.volume])
    span = hi - lo
    if span <= 0:
        return {"symbol": symbol, "status": "BAD_FIRST5"}
    body = abs(cl - op) / span * 100
    range_pct = span / op * 100
    color = "GREEN" if cl > op else "RED" if cl < op else "DOJI"
    gap_pct = (op / levels["close"] - 1) * 100

    pvol = []
    for j in range(ti - 20, ti):
        old = _bars(sessions[j][1], 5)
        if not old.empty:
            pvol.append(float(old.iloc[0].volume))
    avg20 = float(np.mean(pvol)) if pvol else np.nan

    ref = None
    for _, bar in b5.iterrows():
        long_close = float(bar.close) >= levels["resistance"]
        short_close = float(bar.close) <= levels["support"]
        if long_close and short_close:
            return {"symbol": symbol, "status": "AMBIGUOUS_SR"}
        if long_close or short_close:
            ref = (
                "LONG" if long_close else "SHORT",
                levels["resistance"] if long_close else levels["support"],
                bar.date + pd.Timedelta(minutes=5),
            )
            break
    if ref is None:
        return {"symbol": symbol, "status": "NO_SR_CONFIRM", "body": round(body, 2)}

    side, ref_price, actionable = ref
    aligned = color == ("GREEN" if side == "LONG" else "RED")
    extreme_ok = op <= lo + 0.15 * span if side == "LONG" else op >= hi - 0.15 * span
    gap_ok = gap_pct >= -0.25 if side == "LONG" else gap_pct <= 0.25
    opening_checks = {
        "aligned": aligned,
        "body_gte_75": body >= 75,
        "range_lte_2pct": range_pct <= 2,
        "open_near_extreme": extreme_ok,
        "opposing_gap_clear": gap_ok,
        "volume_gte_avg20": bool(np.isfinite(avg20) and vol >= avg20),
        "sr_confirmed": True,
    }
    failed = [name for name, ok in opening_checks.items() if not ok]
    buy, sell = _gann(ref_price)
    trigger = buy if side == "LONG" else sell
    base = {
        "symbol": symbol,
        "side": side,
        "status": "OPEN_FAIL" if failed else "WATCHING",
        "failed": failed,
        "body": round(body, 2),
        "gap_pct": round(gap_pct, 2),
        "volume_ratio": round(vol / avg20, 2) if np.isfinite(avg20) and avg20 else None,
        "reference_price": round(ref_price, 2),
        "gann_trigger": trigger,
        "checks": opening_checks,
    }
    if failed:
        return base

    for pos, row in day[day.date >= actionable].iterrows():
        if pos <= 0:
            continue
        prior = day.iloc[pos - 1]
        actual = float(row.low) <= trigger <= float(row.high)
        fresh = float(prior.close) < trigger if side == "LONG" else float(prior.close) > trigger
        if not (actual and fresh):
            continue
        completed5 = all_b5[(all_b5.date + pd.Timedelta(minutes=5)) <= row.date]
        rsi1 = float(prior.rsi14)
        rsi5 = float(completed5.iloc[-1].rsi14) if not completed5.empty else np.nan
        fib_ok = trigger > levels["midpoint"] and trigger > levels["fib_upper_382"] if side == "LONG" else trigger < levels["midpoint"] and trigger < levels["fib_lower_382"]
        rsi_ok = np.isfinite(rsi1) and np.isfinite(rsi5) and (rsi1 > 72 and rsi5 > 70 if side == "LONG" else rsi1 < 28 and rsi5 < 30)
        window_ok = time(9, 20) <= row.date.time() <= time(10, 59) or time(14, 0) <= row.date.time() <= time(14, 30)
        entry_checks = {**opening_checks, "actual_gann_touch": True, "fresh_cross_no_retest": True, "fibonacci": bool(fib_ok), "rsi_1m_and_5m": bool(rsi_ok), "entry_window": bool(window_ok)}
        reasons = [name for name in ("fibonacci", "rsi_1m_and_5m", "entry_window") if not entry_checks[name]]
        if not reasons:
            target = trigger * (1.01 if side == "LONG" else 0.99)
            stop = trigger * (0.9925 if side == "LONG" else 1.0075)
            return {
                **base,
                "status": "ALERT",
                "entry_time": str(row.date),
                "entry": trigger,
                "target": round(target, 2),
                "stop": round(stop, 2),
                "rsi1": round(rsi1, 2),
                "rsi5": round(rsi5, 2),
                "checks": entry_checks,
                "signal_key": f"STOCKA|{target_day}|{symbol}|{side}|{trigger}|{row.date}",
            }
        return {**base, "status": "FIRST_CROSS_REJECTED", "failed": reasons, "cross_time": str(row.date), "rsi1": round(rsi1, 2), "rsi5": round(rsi5, 2), "checks": entry_checks}
    return base


def _universe() -> list[str]:
    url = "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    text_data = urllib.request.urlopen(req, timeout=20).read().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text_data)))
    return [row.get("Symbol", "").strip().upper() for row in rows if row.get("Symbol")]


def _stock_worker():
    global _STOCK_CACHE
    if not _LOCK.acquire(blocking=False):
        return
    try:
        started = datetime.now(IST)
        _STOCK_CACHE = {"status": "RUNNING", "started_at": started.isoformat(), "updated_at": None, "scanned": 0, "total": 100, "qualified": [], "candidates": [], "errors": []}
        symbols = _universe()
        instruments = client().instruments("NSE")
        token_map = {str(x.get("tradingsymbol", "")).upper(): int(x["instrument_token"]) for x in instruments}
        target = started.date()
        begin = target - timedelta(days=45)
        qualified, candidates, errors = [], [], []
        for index, symbol in enumerate(symbols, 1):
            try:
                token = token_map.get(symbol)
                if token is None:
                    raise RuntimeError("instrument token unavailable")
                raw = fetch_minutes(token, begin, target)
                result = scan_stock_aplus(symbol, raw, target)
                if result.get("status") == "ALERT":
                    qualified.append(result)
                elif result.get("status") in ("WATCHING", "FIRST_CROSS_REJECTED"):
                    candidates.append(result)
            except Exception as exc:
                errors.append({"symbol": symbol, "error": str(exc)[:180]})
            _STOCK_CACHE.update({"scanned": index, "total": len(symbols), "qualified": qualified, "candidates": candidates[-30:], "errors": errors[-10:]})
            pytime.sleep(0.34)
        _STOCK_CACHE.update({"status": "COMPLETE", "updated_at": datetime.now(IST).isoformat(), "qualified": qualified, "candidates": candidates, "errors": errors[-20:]})
    except Exception as exc:
        _STOCK_CACHE.update({"status": "ERROR", "updated_at": datetime.now(IST).isoformat(), "error": str(exc)})
    finally:
        _LOCK.release()


@router.get("/nifty39")
def nifty39_alert():
    try:
        today = datetime.now(IST).date()
        raw = fetch_minutes(NIFTY_TOKEN, today - timedelta(days=45), today)
        return scan_nifty39(raw)
    except Exception as exc:
        raise HTTPException(400, f"NIFTY 39 scan failed: {exc}")


@router.post("/stocks/refresh")
def refresh_stock_alerts():
    if _LOCK.locked():
        return _STOCK_CACHE
    threading.Thread(target=_stock_worker, daemon=True).start()
    return {**_STOCK_CACHE, "status": "STARTING"}


@router.get("/stocks")
def stock_alerts():
    return _STOCK_CACHE


@router.get("/rules")
def alert_rules():
    return {
        "nifty39": {
            "research": "39 trades / 35 wins / 4 losses / +2123.49 points",
            "entry": "Actual Gann touch; retests allowed; prior completed 1-minute RSI >68 LONG / <32 SHORT; previous-day Fib; aligned first 5-minute candle",
            "windows": "09:18–10:59 and 14:00–14:30 IST",
            "gap_exception": "Skip LONG only for gap >=+100 + red first 5m + body >=60%; inverse for SHORT",
            "exit": "+200/-200; after +50, dynamic 3-minute Fib 0.618 exit; otherwise EOD",
        },
        "stock_aplus": {
            "research": "11 qualifying trades / 11 +1% targets in the locked research set",
            "entry": "Completed 5m S/R confirmation, actual Gann touch, fresh cross only/no retest, RSI 1m 72/28 and 5m 70/30, previous-day Fib",
            "opening": "Aligned first 5m; body >=75%; range <=2%; open within correct 15%; opposing gap <=0.25%; volume >= 20-session opening average",
            "windows": "09:20–10:59 and 14:00–14:30 IST",
            "exit": "+1.00% target / -0.75% stop",
        },
    }
