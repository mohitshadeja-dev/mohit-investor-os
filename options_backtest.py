from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta


DHAN_URL = "https://api.dhan.co/v2/charts/rollingoption"
NIFTY_SECURITY_ID = 13


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def option_delta(spot: float, strike: float, iv_pct: float, expiry: datetime, at: datetime, kind: str) -> float:
    """Black-Scholes delta used only to select the closest traded strike."""
    t = max((expiry - at).total_seconds() / (365.0 * 86400.0), 1.0 / (365.0 * 24.0 * 60.0))
    sigma = max(float(iv_pct or 0.0) / 100.0, 0.01)
    d1 = (math.log(max(spot, 0.01) / max(strike, 0.01)) + (0.06 + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    return _normal_cdf(d1) if kind == "CALL" else _normal_cdf(d1) - 1.0


def next_tuesday(at: datetime) -> datetime:
    days = (1 - at.weekday()) % 7
    d = (at + timedelta(days=days)).date()
    return datetime.combine(d, datetime.strptime("15:30", "%H:%M").time(), tzinfo=at.tzinfo)


def _chunks(start: date, end: date):
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=29), end)
        yield cursor, stop + timedelta(days=1)
        cursor = stop + timedelta(days=1)


def _post(access_token: str, body: dict) -> dict:
    req = urllib.request.Request(
        DHAN_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json", "access-token": access_token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Dhan data error HTTP {exc.code}: {detail[:300]}") from exc
    if payload.get("status") == "failure" or payload.get("errorCode"):
        raise RuntimeError(payload.get("errorMessage") or payload.get("message") or str(payload))
    return payload


def fetch_rolling(access_token: str, start: date, end: date, kind: str, offset: int, cache_get, cache_set) -> list[dict]:
    strike = "ATM" if offset == 0 else f"ATM{offset:+d}"
    rows: list[dict] = []
    for chunk_start, chunk_end in _chunks(start, end):
        key = f"WEEK|{kind}|{strike}|{chunk_start}|{chunk_end}"
        payload = cache_get(key)
        if payload is None:
            payload = _post(access_token, {
                "exchangeSegment": "NSE_FNO", "interval": "1", "securityId": NIFTY_SECURITY_ID,
                "instrument": "OPTIDX", "expiryFlag": "WEEK", "expiryCode": 1,
                "strike": strike, "drvOptionType": kind,
                "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
                "fromDate": str(chunk_start), "toDate": str(chunk_end),
            })
            cache_set(key, payload)
            time.sleep(0.12)
        block = (payload.get("data") or {}).get("ce" if kind == "CALL" else "pe") or {}
        stamps = block.get("timestamp") or []
        for i, stamp in enumerate(stamps):
            def val(name, default=0.0):
                arr = block.get(name) or []
                return arr[i] if i < len(arr) and arr[i] is not None else default
            rows.append({"timestamp": int(stamp), "close": float(val("close")), "iv": float(val("iv")),
                         "strike": float(val("strike")), "spot": float(val("spot")), "offset": offset})
    return rows


def run_options_backtest(signal_trades: list[dict], access_token: str, quantity: int, short_delta: float,
                         hedge_delta: float, cache_get, cache_set, progress=lambda *_: None) -> tuple[dict, list[dict]]:
    if not signal_trades:
        return {"trades": 0, "total_pnl": 0}, []
    start = date.fromisoformat(min(t["date"] for t in signal_trades))
    end = date.fromisoformat(max(t["date"] for t in signal_trades))
    # Fetch the complete weekly chain around ATM. This lets the exit lookup keep the exact entry strike,
    # even when that strike's ATM offset changes as NIFTY moves.
    chain: dict[str, dict[int, list[dict]]] = {"CALL": {}, "PUT": {}}
    total = 42
    done = 0
    for kind in ("CALL", "PUT"):
        for offset in range(-10, 11):
            for row in fetch_rolling(access_token, start, end, kind, offset, cache_get, cache_set):
                chain[kind].setdefault(row["timestamp"], []).append(row)
            done += 1
            progress(done, total, f"Loading weekly {kind} {('ATM' if offset == 0 else f'ATM{offset:+d}')}")

    def epoch(value: str) -> int:
        dt = datetime.fromisoformat(value)
        return int(dt.timestamp())

    def nearest_rows(kind: str, stamp: int) -> list[dict]:
        book = chain[kind]
        if stamp in book:
            return book[stamp]
        keys = [k for k in book if abs(k - stamp) <= 120]
        return book[min(keys, key=lambda k: abs(k - stamp))] if keys else []

    ledger = []
    equity = peak = max_dd = 0.0
    for idx, trade in enumerate(signal_trades, 1):
        kind = "CALL" if trade["side"] == "SHORT" else "PUT"
        entry_stamp, exit_stamp = epoch(trade["entry_time"]), epoch(trade["exit_time"])
        entry_at = datetime.fromisoformat(trade["entry_time"])
        candidates = nearest_rows(kind, entry_stamp)
        if not candidates:
            ledger.append({**trade, "option_status": "NO ENTRY OPTION DATA"})
            continue
        expiry = next_tuesday(entry_at)
        for row in candidates:
            row["delta"] = option_delta(row["spot"], row["strike"], row["iv"], expiry, entry_at, kind)
        target_short = short_delta if kind == "CALL" else -short_delta
        target_hedge = hedge_delta if kind == "CALL" else -hedge_delta
        short_leg = min(candidates, key=lambda r: abs(r["delta"] - target_short))
        hedge_leg = min(candidates, key=lambda r: abs(r["delta"] - target_hedge))
        exits = nearest_rows(kind, exit_stamp)
        short_exit = next((r for r in exits if r["strike"] == short_leg["strike"]), None)
        hedge_exit = next((r for r in exits if r["strike"] == hedge_leg["strike"]), None)
        if not short_exit or not hedge_exit:
            ledger.append({**trade, "option_status": "NO FIXED-STRIKE EXIT DATA"})
            continue
        credit = short_leg["close"] - hedge_leg["close"]
        exit_value = short_exit["close"] - hedge_exit["close"]
        points = credit - exit_value
        pnl = points * quantity
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        ledger.append({
            "date": trade["date"], "trade_no": trade["trade_no"], "underlying_side": trade["side"],
            "entry_time": trade["entry_time"], "exit_time": trade["exit_time"], "option_type": kind,
            "short_strike": short_leg["strike"], "short_entry": round(short_leg["close"], 2),
            "short_delta": round(short_leg["delta"], 3), "short_exit": round(short_exit["close"], 2),
            "hedge_strike": hedge_leg["strike"], "hedge_entry": round(hedge_leg["close"], 2),
            "hedge_delta": round(hedge_leg["delta"], 3), "hedge_exit": round(hedge_exit["close"], 2),
            "entry_credit": round(credit, 2), "exit_value": round(exit_value, 2),
            "spread_points": round(points, 2), "quantity": quantity, "pnl": round(pnl, 2),
            "underlying_exit_reason": trade.get("reason"), "option_status": "OK",
        })
        progress(total, total, f"Pricing linked trade {idx}/{len(signal_trades)}")
    valid = [x for x in ledger if x.get("option_status") == "OK"]
    wins = [x for x in valid if x["pnl"] > 0]
    gross_win = sum(x["pnl"] for x in valid if x["pnl"] > 0)
    gross_loss = abs(sum(x["pnl"] for x in valid if x["pnl"] < 0))
    summary = {
        "trades": len(valid), "missing": len(ledger) - len(valid), "wins": len(wins),
        "win_rate": round(100 * len(wins) / len(valid), 2) if valid else 0,
        "total_pnl": round(sum(x["pnl"] for x in valid), 2),
        "avg_pnl": round(sum(x["pnl"] for x in valid) / len(valid), 2) if valid else 0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "max_drawdown": round(max_dd, 2), "quantity": quantity,
    }
    return summary, ledger
