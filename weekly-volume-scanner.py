from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf


IST = ZoneInfo("Asia/Kolkata")
CACHE_PATH = Path(os.getenv("WEEKLY_VOLUME_CACHE", "/data/weekly-volume-scanner.json"))
LOCK = threading.Lock()
MAX_MARKET_CAP = 50_000_000_000  # INR 5,000 crore


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _cached_today():
    try:
        payload = json.loads(CACHE_PATH.read_text())
        if payload.get("scan_date") == datetime.now(IST).date().isoformat():
            return payload
    except Exception:
        pass
    return None


def _india_smallcap_universe():
    """Yahoo India equities at or below INR 5,000 crore, paginated."""
    query = yf.EquityQuery("and", [
        yf.EquityQuery("eq", ["region", "in"]),
        yf.EquityQuery("is-in", ["exchange", "NSI", "BSE"]),
        yf.EquityQuery("gt", ["intradaymarketcap", 0]),
        yf.EquityQuery("lte", ["intradaymarketcap", MAX_MARKET_CAP]),
        yf.EquityQuery("gt", ["intradayprice", 1]),
    ])
    found = {}
    for offset in range(0, 2000, 250):
        page = yf.screen(query, offset=offset, size=250,
                         sortField="dayvolume", sortAsc=False)
        quotes = page.get("quotes", []) if isinstance(page, dict) else []
        if not quotes:
            break
        for q in quotes:
            symbol = str(q.get("symbol") or "").strip().upper()
            cap = _number(q.get("marketCap") or q.get("intradaymarketcap"))
            if symbol and cap and cap <= MAX_MARKET_CAP:
                found[symbol] = {
                    "symbol": symbol,
                    "company": q.get("longName") or q.get("shortName") or symbol,
                    "market_cap_crore": round(cap / 10_000_000, 1),
                }
        if len(quotes) < 250:
            break
    return found


def _frame_for(download, symbol, batch_size):
    if batch_size == 1:
        return download
    if not isinstance(download.columns, pd.MultiIndex):
        return pd.DataFrame()
    # yfinance can return either (Price, Ticker) or (Ticker, Price).
    if symbol in download.columns.get_level_values(0):
        return download[symbol]
    if symbol in download.columns.get_level_values(1):
        return download.xs(symbol, axis=1, level=1)
    return pd.DataFrame()


def _scan_batch(symbols, meta):
    result = []
    raw = yf.download(symbols, period="max", interval="1wk", auto_adjust=False,
                      progress=False, threads=True, group_by="ticker")
    for symbol in symbols:
        try:
            frame = _frame_for(raw, symbol, len(symbols))
            frame = frame.dropna(subset=["Close", "High", "Volume"])
            if len(frame) < 12:
                continue
            latest = frame.iloc[-1]
            previous = frame.iloc[:-1]
            latest_volume = _number(latest["Volume"])
            prior_record = _number(previous["Volume"].max())
            if not latest_volume or not prior_record or latest_volume < prior_record:
                continue
            close = _number(latest["Close"])
            ath = _number(frame["High"].max())
            if close is None or ath is None:
                continue
            peak_idx = frame["Volume"].idxmax()
            points = max(0.0, ath - close)
            row = dict(meta[symbol])
            row.update({
                "latest_weekly_volume": latest_volume,
                "previous_lifetime_weekly_record": prior_record,
                "record_multiple": round(latest_volume / prior_record, 2),
                "record_week": peak_idx.strftime("%Y-%m-%d"),
                "close": round(close, 2),
                "all_time_high": round(ath, 2),
                "points_below_all_time_high": round(points, 2),
                "percent_below_all_time_high": round(points / ath * 100, 2) if ath else None,
            })
            result.append(row)
        except Exception:
            continue
    return result


def scan_lifetime_weekly_volume(force=False):
    if not force:
        cached = _cached_today()
        if cached:
            return cached
    with LOCK:
        if not force:
            cached = _cached_today()
            if cached:
                return cached
        meta = _india_smallcap_universe()
        symbols = sorted(meta)
        matches = []
        for start in range(0, len(symbols), 75):
            batch = symbols[start:start + 75]
            matches.extend(_scan_batch(batch, meta))
        matches.sort(key=lambda x: (-x["record_multiple"],
                                   x["percent_below_all_time_high"]))
        payload = {
            "scan_date": datetime.now(IST).date().isoformat(),
            "updated_at": datetime.now(IST).isoformat(),
            "market_cap_limit_crore": 5000,
            "universe_count": len(symbols),
            "match_count": len(matches),
            "items": matches,
            "method": "Yahoo India equity universe; current weekly volume compared with every prior weekly candle in available history.",
        }
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(payload))
        except Exception:
            pass
        return payload
