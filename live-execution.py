from __future__ import annotations

import math
import threading
import time
import uuid
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
TICKETS: dict[str, dict] = {}
SPREADS: dict[str, dict] = {}
LOCK = threading.RLock()
TICKET_TTL_SECONDS = 90


class LiveOrderError(RuntimeError):
    pass


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_price(spot: float, strike: float, years: float, rate: float, vol: float, kind: str) -> float:
    if min(spot, strike, years, vol) <= 0:
        return 0.0
    root_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * root_t)
    d2 = d1 - vol * root_t
    disc = math.exp(-rate * years)
    if kind == "CE":
        return spot * _normal_cdf(d1) - strike * disc * _normal_cdf(d2)
    return strike * disc * _normal_cdf(-d2) - spot * _normal_cdf(-d1)


def _delta(spot: float, strike: float, years: float, rate: float, vol: float, kind: str) -> float:
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * math.sqrt(years))
    return _normal_cdf(d1) if kind == "CE" else _normal_cdf(d1) - 1.0


def _implied_vol(price: float, spot: float, strike: float, years: float, rate: float, kind: str) -> float | None:
    intrinsic = max(0.0, spot - strike) if kind == "CE" else max(0.0, strike - spot)
    if price <= intrinsic or price <= 0 or years <= 0:
        return None
    lo, hi = 0.01, 5.0
    if _bs_price(spot, strike, years, rate, hi, kind) < price:
        return None
    for _ in range(70):
        mid = (lo + hi) / 2
        if _bs_price(spot, strike, years, rate, mid, kind) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _prune() -> None:
    cutoff = time.time() - 3600
    for key in [k for k, v in TICKETS.items() if v.get("created_epoch", 0) < cutoff]:
        TICKETS.pop(key, None)


def _quote_batches(kite, keys: list[str]) -> dict:
    out = {}
    for i in range(0, len(keys), 400):
        out.update(kite.quote(keys[i:i + 400]))
    return out


def _wait_complete(kite, order_id: str, timeout_seconds: float = 10.0) -> dict:
    deadline = time.time() + timeout_seconds
    last = {}
    while time.time() < deadline:
        rows = kite.order_history(order_id)
        if rows:
            last = rows[-1]
            status = str(last.get("status") or "").upper()
            if status == "COMPLETE":
                return last
            if status in {"REJECTED", "CANCELLED"}:
                raise LiveOrderError(f'Hedge order {status.lower()}: {last.get("status_message") or "broker rejected it"}')
        time.sleep(.4)
    raise LiveOrderError(f'Hedge order was not confirmed filled within {timeout_seconds:.0f} seconds; short leg was not sent')


def _select_contracts(kite, signal: str, sell_delta: float, buy_delta: float) -> tuple[dict, dict, dict]:
    side = signal.strip().upper()
    if side not in {"LONG", "SHORT"}:
        raise LiveOrderError("Signal must be LONG or SHORT")
    kind = "PE" if side == "LONG" else "CE"
    spot_quote = kite.ltp(["NSE:NIFTY 50"]).get("NSE:NIFTY 50", {})
    spot = float(spot_quote.get("last_price") or 0)
    if spot <= 0:
        raise LiveOrderError("NIFTY spot quote is unavailable")

    today = datetime.now(IST).date()
    contracts = []
    for row in kite.instruments("NFO"):
        expiry = row.get("expiry")
        if row.get("name") != "NIFTY" or row.get("instrument_type") != kind or not expiry or expiry < today:
            continue
        if abs(float(row.get("strike") or 0) - spot) <= spot * 0.12:
            contracts.append(row)
    if not contracts:
        raise LiveOrderError(f"No active NIFTY {kind} contracts found")
    expiry = min(r["expiry"] for r in contracts)
    contracts = [r for r in contracts if r["expiry"] == expiry]
    expiry_at = datetime.combine(expiry, dtime(15, 30), tzinfo=IST)
    years = max((expiry_at - datetime.now(IST)).total_seconds() / (365.0 * 86400.0), 1.0 / (365.0 * 1440.0))
    keys = [f'NFO:{r["tradingsymbol"]}' for r in contracts]
    quotes = _quote_batches(kite, keys)
    candidates = []
    for row in contracts:
        key = f'NFO:{row["tradingsymbol"]}'
        q = quotes.get(key, {})
        ltp = float(q.get("last_price") or 0)
        if ltp <= 0:
            continue
        iv = _implied_vol(ltp, spot, float(row["strike"]), years, .06, kind)
        if iv is None:
            continue
        delta = abs(_delta(spot, float(row["strike"]), years, .06, iv, kind))
        candidates.append({**row, "ltp": round(ltp, 2), "delta": round(delta, 4), "iv": round(iv, 4), "quote_key": key})
    if not candidates:
        raise LiveOrderError("Could not calculate deltas from the live NIFTY option chain")
    short_leg = min(candidates, key=lambda x: abs(x["delta"] - sell_delta))
    hedge_leg = min(candidates, key=lambda x: abs(x["delta"] - buy_delta))
    if short_leg["tradingsymbol"] == hedge_leg["tradingsymbol"]:
        raise LiveOrderError("Delta selection resolved to the same strike; wait for a valid chain")
    meta = {"signal": side, "option_type": kind, "spot": round(spot, 2), "expiry": str(expiry), "years": years}
    return short_leg, hedge_leg, meta


def build_ticket(kite, signal: str, quantity: int, sell_delta: float = .70, buy_delta: float = .30) -> dict:
    with LOCK:
        _prune()
        short_leg, hedge_leg, meta = _select_contracts(kite, signal, sell_delta, buy_delta)
        lot_size = int(short_leg.get("lot_size") or 0)
        if lot_size <= 0 or quantity % lot_size:
            raise LiveOrderError(f"Quantity {quantity} must be a multiple of the current NIFTY lot size ({lot_size})")
        if int(hedge_leg.get("lot_size") or 0) != lot_size:
            raise LiveOrderError("Selected contracts have inconsistent lot sizes")
        ticket_id = uuid.uuid4().hex
        ticket = {
            "ticket_id": ticket_id, "created_epoch": time.time(), "expires_in_seconds": TICKET_TTL_SECONDS,
            "status": "PREVIEW", "quantity": quantity, "lots": quantity // lot_size, "lot_size": lot_size,
            "sell_delta_target": sell_delta, "buy_delta_target": buy_delta, **meta,
            "short_leg": {"tradingsymbol": short_leg["tradingsymbol"], "strike": float(short_leg["strike"]), "delta": short_leg["delta"], "ltp": short_leg["ltp"], "action": "SELL"},
            "hedge_leg": {"tradingsymbol": hedge_leg["tradingsymbol"], "strike": float(hedge_leg["strike"]), "delta": hedge_leg["delta"], "ltp": hedge_leg["ltp"], "action": "BUY"},
            "estimated_credit": round((short_leg["ltp"] - hedge_leg["ltp"]) * quantity, 2),
            "confirmation_required": "PLACE",
        }
        TICKETS[ticket_id] = ticket
        return ticket


def ticket_snapshot(ticket_id: str) -> dict:
    """Return the server-side ticket used for placement validation."""
    with LOCK:
        ticket = TICKETS.get(ticket_id)
        if not ticket:
            raise LiveOrderError("Ticket not found or expired; prepare it again")
        return dict(ticket)


def place_spread(kite, ticket_id: str, signal: str, quantity: int, sell_delta: float, buy_delta: float, confirmation: str) -> dict:
    required_confirmation = f"PLACE {quantity}"
    if confirmation.strip().upper() != required_confirmation:
        raise LiveOrderError(f'Type {required_confirmation} in the confirmation box')
    with LOCK:
        ticket = TICKETS.get(ticket_id)
        if not ticket:
            raise LiveOrderError("Ticket not found or expired; prepare it again")
        if ticket["status"] != "PREVIEW":
            raise LiveOrderError(f'Ticket is already {ticket["status"]}')
        if time.time() - ticket["created_epoch"] > TICKET_TTL_SECONDS:
            ticket["status"] = "EXPIRED"
            raise LiveOrderError("Quote expired; prepare a fresh ticket")
        if (ticket["signal"] != signal.strip().upper() or ticket["quantity"] != quantity or
                ticket["sell_delta_target"] != sell_delta or ticket["buy_delta_target"] != buy_delta):
            raise LiveOrderError("Ticket inputs changed; prepare a fresh ticket")
        ticket["status"] = "SUBMITTING"
        common = dict(variety=kite.VARIETY_REGULAR, exchange=kite.EXCHANGE_NFO, quantity=quantity,
                      product=kite.PRODUCT_MIS, order_type=kite.ORDER_TYPE_MARKET, validity=kite.VALIDITY_DAY)
        try:
            hedge_id = kite.place_order(tradingsymbol=ticket["hedge_leg"]["tradingsymbol"], transaction_type=kite.TRANSACTION_TYPE_BUY, tag="MIOHEDGE", **common)
            hedge_fill = _wait_complete(kite, hedge_id)
            short_id = kite.place_order(tradingsymbol=ticket["short_leg"]["tradingsymbol"], transaction_type=kite.TRANSACTION_TYPE_SELL, tag="MIOSHORT", **common)
        except Exception:
            ticket["status"] = "PARTIAL_OR_FAILED"
            raise
        spread_id = uuid.uuid4().hex
        spread = {**ticket, "spread_id": spread_id, "status": "ORDERS_SENT", "hedge_order_id": hedge_id, "hedge_fill_price": hedge_fill.get("average_price"), "short_order_id": short_id, "placed_at": datetime.now(IST).isoformat()}
        ticket["status"] = "PLACED"
        SPREADS[spread_id] = spread
        return spread


def close_spread_record(kite, spread: dict) -> dict:
    with LOCK:
        if spread["status"] in {"EXIT_SENT", "CLOSED"}:
            raise LiveOrderError("Exit was already submitted")
        common = dict(variety=kite.VARIETY_REGULAR, exchange=kite.EXCHANGE_NFO, quantity=spread["quantity"],
                      product=kite.PRODUCT_MIS, order_type=kite.ORDER_TYPE_MARKET, validity=kite.VALIDITY_DAY)
        # Remove the short exposure and confirm that BUY fill before selling the hedge.
        if spread["status"] == "ORDERS_SENT":
            short_exit = kite.place_order(tradingsymbol=spread["short_leg"]["tradingsymbol"], transaction_type=kite.TRANSACTION_TYPE_BUY, tag="MIOEXIT", **common)
            short_fill = _wait_complete(kite,short_exit)
            spread.update({"status":"SHORT_CLOSED","short_exit_order_id":short_exit,"short_exit_price":short_fill.get("average_price")})
        hedge_exit = kite.place_order(tradingsymbol=spread["hedge_leg"]["tradingsymbol"], transaction_type=kite.TRANSACTION_TYPE_SELL, tag="MIOEXIT", **common)
        hedge_fill = _wait_complete(kite,hedge_exit)
        spread.update({"status": "CLOSED", "hedge_exit_order_id": hedge_exit, "hedge_exit_price":hedge_fill.get("average_price"), "exit_sent_at": datetime.now(IST).isoformat()})
        if spread.get("spread_id"):SPREADS[spread["spread_id"]]=spread
        return spread


def close_spread(kite, spread_id: str, confirmation: str) -> dict:
    if confirmation.strip().upper() != "EXIT":
        raise LiveOrderError('Type EXIT in the confirmation box')
    spread = SPREADS.get(spread_id)
    if not spread:
        raise LiveOrderError("Spread was not found in this server session")
    return close_spread_record(kite,spread)


def order_book(kite) -> dict:
    return {"broker_orders": kite.orders(), "spreads": list(SPREADS.values())}
