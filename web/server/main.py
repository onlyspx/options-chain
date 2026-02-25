"""
FastAPI server for SPX 0DTE dashboard.
Serves GET /api/snapshot (chain + quote) and static frontend from ../frontend/dist.
Run from repo root: uvicorn web.server.main:app --reload
"""
import os
import sys
from collections import deque
from datetime import date, datetime, timedelta
from decimal import Decimal

# Repo root and scripts dir so we can import config and get_option_chain (they use "from config import")
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
for _p in (_SCRIPTS_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Import after path is set (scripts use "from config import")
from config import get_api_secret, get_account_id
from get_option_chain import get_option_expirations, parse_osi_symbol

try:
    from public_api_sdk import (
        PublicApiClient,
        PublicApiClientConfiguration,
        OrderInstrument,
        InstrumentType,
        OptionChainRequest,
    )
    from public_api_sdk.auth_config import ApiKeyAuthConfig
except ImportError:
    PublicApiClient = None

app = FastAPI(title="SPX 0DTE Dashboard API")

DEFAULT_SYMBOL = "SPX"
SUPPORTED_SYMBOLS = {
    "SPX": "INDEX",
    "NDX": "INDEX",
    "SPY": "EQUITY",
    "QQQ": "EQUITY",
}
DEFAULT_ATM_STRIKES = 15  # fallback ATM ± N strikes
MID_DTE_ATM_STRIKES = 25  # for 2-4 days to expiry
LONG_DTE_ATM_STRIKES = 35  # for 5+ days to expiry
QUOTE_REFRESH_SECONDS = 10
CHAIN_REFRESH_SECONDS = 60
SNAPSHOT_BUFFER_MAX_AGE_MINUTES = 5
HOT_STRIKES_TOP_N = 8
SUPPORTED_DTES = {0, 1}
SUPPORTED_EXPIRY_MODES = {"dte", "friday"}
_snapshot_buffers = {}  # (symbol, expiry_mode, dte) -> deque[(iso_ts, strikes_slim: [{strike, put_vol, call_vol}])]
_quote_cache_by_symbol = {}  # symbol -> {fetched_at, symbol_price, timestamp}
_chain_cache_by_symbol_exp = {}  # (symbol, expiration) -> {fetched_at, by_strike, timestamp}


def _norm_exp(exp):
    if hasattr(exp, "strftime"):
        return exp.strftime("%Y-%m-%d")
    return str(exp)


def _resolve_expiration_targets(client, symbol: str, instrument_type):
    """Resolve key expirations used by the UI: 0dte, 1dte, and Friday weekly."""
    expirations = get_option_expirations(client, symbol, instrument_type=instrument_type)
    if not expirations:
        return None
    parsed = []
    for exp in expirations:
        exp_str = _norm_exp(exp)
        try:
            parsed.append(date.fromisoformat(exp_str))
        except ValueError:
            continue
    if not parsed:
        return None
    normalized_dates = sorted(set(parsed))
    today = date.today()
    dte0 = next((exp for exp in normalized_dates if exp >= today), normalized_dates[-1])
    dte1 = next((exp for exp in normalized_dates if exp > today), normalized_dates[-1])
    include_today_for_friday = today.weekday() != 4
    friday = None
    for exp in normalized_dates:
        if exp.weekday() != 4:
            continue
        if include_today_for_friday and exp >= today:
            friday = exp
            break
        if not include_today_for_friday and exp > today:
            friday = exp
            break
    if friday is None:
        friday = dte1
    return {
        "dte0": dte0.isoformat(),
        "dte1": dte1.isoformat(),
        "friday": friday.isoformat(),
    }


def _pick_expiration(exp_targets: dict[str, str], expiry_mode: str, dte: int):
    if expiry_mode == "friday":
        return exp_targets["friday"]
    return exp_targets["dte1"] if dte == 1 else exp_targets["dte0"]


def _decimal_float(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _mid(bid, ask):
    b = _decimal_float(bid)
    a = _decimal_float(ask)
    if b is None or a is None:
        return None
    return round((b + a) / 2, 4)


def _now_utc():
    return datetime.utcnow()


def _iso_utc(ts: datetime):
    return ts.isoformat() + "Z"


def _prune_buffer(snapshot_buffer: deque, now_utc: datetime):
    cutoff = now_utc - timedelta(minutes=SNAPSHOT_BUFFER_MAX_AGE_MINUTES)
    while snapshot_buffer:
        first_ts = datetime.fromisoformat(snapshot_buffer[0][0].replace("Z", ""))
        if first_ts < cutoff:
            snapshot_buffer.popleft()
        else:
            break


def _build_by_strike(calls, puts):
    by_strike = {}
    for opt in calls:
        osi = opt.instrument.symbol if hasattr(opt, "instrument") else ""
        strike = parse_osi_symbol(osi)
        if strike is None:
            continue
        by_strike.setdefault(
            strike,
            {
                "strike": strike,
                "call_oi": None,
                "put_oi": None,
                "call_vol": None,
                "put_vol": None,
                "call_bid": None,
                "call_ask": None,
                "put_bid": None,
                "put_ask": None,
            },
        )
        by_strike[strike]["call_oi"] = getattr(opt, "open_interest", None)
        by_strike[strike]["call_vol"] = getattr(opt, "volume", None)
        by_strike[strike]["call_bid"] = _decimal_float(getattr(opt, "bid", None))
        by_strike[strike]["call_ask"] = _decimal_float(getattr(opt, "ask", None))

    for opt in puts:
        osi = opt.instrument.symbol if hasattr(opt, "instrument") else ""
        strike = parse_osi_symbol(osi)
        if strike is None:
            continue
        by_strike.setdefault(
            strike,
            {
                "strike": strike,
                "call_oi": None,
                "put_oi": None,
                "call_vol": None,
                "put_vol": None,
                "call_bid": None,
                "call_ask": None,
                "put_bid": None,
                "put_ask": None,
            },
        )
        by_strike[strike]["put_oi"] = getattr(opt, "open_interest", None)
        by_strike[strike]["put_vol"] = getattr(opt, "volume", None)
        by_strike[strike]["put_bid"] = _decimal_float(getattr(opt, "bid", None))
        by_strike[strike]["put_ask"] = _decimal_float(getattr(opt, "ask", None))

    return by_strike


def _days_to_expiry(expiration: str):
    try:
        exp_date = date.fromisoformat(expiration)
    except (TypeError, ValueError):
        return None
    return max(0, (exp_date - date.today()).days)


def _strike_window_size(days_to_expiry: int | None):
    if days_to_expiry is None:
        return DEFAULT_ATM_STRIKES
    if days_to_expiry <= 1:
        return DEFAULT_ATM_STRIKES
    if days_to_expiry <= 4:
        return MID_DTE_ATM_STRIKES
    return LONG_DTE_ATM_STRIKES


def _windowed_strikes(by_strike, spx_price, atm_strikes: int = DEFAULT_ATM_STRIKES):
    strikes_list = sorted(by_strike.keys(), reverse=True)
    if spx_price is None or not strikes_list:
        return [by_strike[s] for s in strikes_list]
    atm_idx = min(range(len(strikes_list)), key=lambda i: abs(strikes_list[i] - spx_price))
    lo = max(0, atm_idx - atm_strikes)
    hi = min(len(strikes_list), atm_idx + atm_strikes + 1)
    return [by_strike[s] for s in strikes_list[lo:hi]]


def _get_quote_price(client, now_utc: datetime, symbol: str, instrument_type):
    cache_entry = _quote_cache_by_symbol.setdefault(symbol, {"fetched_at": None, "symbol_price": None, "timestamp": None})
    fetched_at = cache_entry.get("fetched_at")
    if fetched_at is not None and (now_utc - fetched_at).total_seconds() < QUOTE_REFRESH_SECONDS:
        return cache_entry.get("symbol_price"), cache_entry.get("timestamp")
    quotes = client.get_quotes([OrderInstrument(symbol=symbol, type=instrument_type)])
    symbol_price = None
    if quotes and len(quotes) > 0:
        symbol_price = _decimal_float(getattr(quotes[0], "last", None))
    ts = _iso_utc(now_utc)
    cache_entry["fetched_at"] = now_utc
    cache_entry["symbol_price"] = symbol_price
    cache_entry["timestamp"] = ts
    return symbol_price, ts


def _get_chain_data(client, now_utc: datetime, symbol: str, instrument_type, expiration: str):
    cache_key = (symbol, expiration)
    cache_entry = _chain_cache_by_symbol_exp.get(cache_key)
    if cache_entry:
        fetched_at = cache_entry.get("fetched_at")
        if (
            fetched_at is not None
            and (now_utc - fetched_at).total_seconds() < CHAIN_REFRESH_SECONDS
            and cache_entry.get("by_strike")
        ):
            return expiration, cache_entry["by_strike"], cache_entry["timestamp"]
    request = OptionChainRequest(
        instrument=OrderInstrument(symbol=symbol, type=instrument_type),
        expiration_date=expiration,
    )
    chain = client.get_option_chain(request)
    calls = getattr(chain, "calls", []) or []
    puts = getattr(chain, "puts", []) or []
    by_strike = _build_by_strike(calls, puts)
    ts = _iso_utc(now_utc)
    _chain_cache_by_symbol_exp[cache_key] = {
        "fetched_at": now_utc,
        "by_strike": by_strike,
        "timestamp": ts,
    }
    return expiration, by_strike, ts


def _compute_expected_move(by_strike, spx_price):
    if spx_price is None or not by_strike:
        return None
    strikes = sorted(by_strike.keys())
    atm_strike = min(strikes, key=lambda s: abs(s - spx_price))
    row = by_strike.get(atm_strike, {})
    call_mid = _mid(row.get("call_bid"), row.get("call_ask"))
    put_mid = _mid(row.get("put_bid"), row.get("put_ask"))
    if call_mid is None or put_mid is None:
        return None
    expected_move = round(call_mid + put_mid, 2)
    return {
        "em_method": "atm_straddle_mid",
        "em_strike": atm_strike,
        "em_call_mid": round(call_mid, 2),
        "em_put_mid": round(put_mid, 2),
        "expected_move": expected_move,
        "em_low": round(spx_price - expected_move, 2),
        "em_high": round(spx_price + expected_move, 2),
    }


def _compute_hot_strikes(current_rows, snapshot_buffer: deque, target_minutes=5, top_n=HOT_STRIKES_TOP_N):
    if not snapshot_buffer:
        return [], []
    now_utc = _now_utc()
    target = now_utc - timedelta(minutes=target_minutes)
    candidates = list(snapshot_buffer)
    best_ts, best_slim = min(
        candidates,
        key=lambda item: abs((datetime.fromisoformat(item[0].replace("Z", "")) - target).total_seconds()),
    )
    old_by_strike = {s["strike"]: s for s in best_slim}
    hot_calls = []
    hot_puts = []
    for row in current_rows:
        strike = row.get("strike")
        old = old_by_strike.get(strike, {})
        call_now = row.get("call_vol") or 0
        put_now = row.get("put_vol") or 0
        call_old = old.get("call_vol") or 0
        put_old = old.get("put_vol") or 0
        delta_call = call_now - call_old
        delta_put = put_now - put_old
        if delta_call > 0:
            hot_calls.append(
                {
                    "strike": strike,
                    "current_vol": call_now,
                    "vol_5m_ago": call_old,
                    "delta_5m": delta_call,
                    "snapshot_ref": best_ts,
                }
            )
        if delta_put > 0:
            hot_puts.append(
                {
                    "strike": strike,
                    "current_vol": put_now,
                    "vol_5m_ago": put_old,
                    "delta_5m": delta_put,
                    "snapshot_ref": best_ts,
                }
            )
    hot_calls.sort(key=lambda x: x["delta_5m"], reverse=True)
    hot_puts.sort(key=lambda x: x["delta_5m"], reverse=True)
    return hot_calls[:top_n], hot_puts[:top_n]


def _compute_spread_scanner(by_strike, spx_price):
    if spx_price is None or not by_strike:
        return {"call_credit_spreads": [], "put_credit_spreads": []}
    strikes = sorted(by_strike.keys())

    def spread_entry(side, short_strike, long_strike):
        short_row = by_strike.get(short_strike, {})
        long_row = by_strike.get(long_strike, {})
        if side == "call":
            short_bid = _decimal_float(short_row.get("call_bid"))
            short_ask = _decimal_float(short_row.get("call_ask"))
            long_bid = _decimal_float(long_row.get("call_bid"))
            long_ask = _decimal_float(long_row.get("call_ask"))
            short_vol = short_row.get("call_vol")
            long_vol = long_row.get("call_vol")
            short_oi = short_row.get("call_oi")
            long_oi = long_row.get("call_oi")
        else:
            short_bid = _decimal_float(short_row.get("put_bid"))
            short_ask = _decimal_float(short_row.get("put_ask"))
            long_bid = _decimal_float(long_row.get("put_bid"))
            long_ask = _decimal_float(long_row.get("put_ask"))
            short_vol = short_row.get("put_vol")
            long_vol = long_row.get("put_vol")
            short_oi = short_row.get("put_oi")
            long_oi = long_row.get("put_oi")
        if None in (short_bid, short_ask, long_bid, long_ask):
            return None
        bid_credit = round(short_bid - long_ask, 2)
        ask_credit = round(short_ask - long_bid, 2)
        mark_credit = round(_mid(short_bid, short_ask) - _mid(long_bid, long_ask), 2)
        # Let frontend control credit-range filtering; keep only sensible positive credits.
        if mark_credit <= 0:
            return None
        return {
            "side": side,
            "short_strike": short_strike,
            "long_strike": long_strike,
            "width": round(abs(long_strike - short_strike), 2),
            "distance_from_spx": round(abs(short_strike - spx_price), 2),
            "bid_credit": bid_credit,
            "ask_credit": ask_credit,
            "mark_credit": mark_credit,
            "short_volume": short_vol,
            "long_volume": long_vol,
            "short_oi": short_oi,
            "long_oi": long_oi,
        }

    call_spreads = []
    put_spreads = []
    for i in range(len(strikes) - 1):
        lower = strikes[i]
        higher = strikes[i + 1]

        # Adjacent-ladder call credit: short lower OTM call, long next higher strike.
        if lower > spx_price:
            entry = spread_entry("call", lower, higher)
            if entry:
                call_spreads.append(entry)

        # Adjacent-ladder put credit: short higher OTM put, long next lower strike.
        if higher < spx_price:
            entry = spread_entry("put", higher, lower)
            if entry:
                put_spreads.append(entry)

    # "Far OTM" intent: show the farthest spreads first, then richer credits.
    call_spreads.sort(key=lambda x: (-x["distance_from_spx"], -x["mark_credit"], -x["short_strike"]))
    put_spreads.sort(key=lambda x: (-x["distance_from_spx"], -x["mark_credit"], -x["short_strike"]))
    return {
        "call_credit_spreads": call_spreads,
        "put_credit_spreads": put_spreads,
    }


def _resolve_account_id(secret: str, account_id: str | None) -> str:
    """Resolve account id from env, or auto-discover the first account."""
    if account_id:
        return account_id
    # Fallback for hosted environments: discover account id from API when env var is absent.
    discover_client = PublicApiClient(
        ApiKeyAuthConfig(api_secret_key=secret),
        config=PublicApiClientConfiguration(),
    )
    try:
        accounts_response = discover_client.get_accounts()
        accounts = getattr(accounts_response, "accounts", None) or []
        if not accounts:
            raise HTTPException(status_code=500, detail="No Public.com accounts found for API key")
        first_account_id = getattr(accounts[0], "account_id", None)
        if not first_account_id:
            raise HTTPException(status_code=500, detail="Could not resolve account id from accounts response")
        return str(first_account_id)
    finally:
        discover_client.close()


def _fetch_snapshot(symbol: str = DEFAULT_SYMBOL, dte: int = 0, expiry_mode: str = "dte"):
    if dte not in SUPPORTED_DTES:
        raise HTTPException(status_code=400, detail=f"Unsupported dte={dte}; expected one of {sorted(SUPPORTED_DTES)}")
    expiry_mode = expiry_mode.lower()
    if expiry_mode not in SUPPORTED_EXPIRY_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported expiry_mode={expiry_mode}; expected one of {sorted(SUPPORTED_EXPIRY_MODES)}",
        )
    symbol = symbol.upper()
    if symbol not in SUPPORTED_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported symbol={symbol}; expected one of {sorted(SUPPORTED_SYMBOLS)}",
        )
    secret = get_api_secret()
    account_id = get_account_id()
    if not secret:
        raise HTTPException(status_code=500, detail="PUBLIC_COM_SECRET not set")
    if PublicApiClient is None:
        raise HTTPException(status_code=500, detail="publicdotcom-py not installed")
    account_id = _resolve_account_id(secret=secret, account_id=account_id)
    instrument_type = (
        InstrumentType.INDEX
        if SUPPORTED_SYMBOLS[symbol] == "INDEX"
        else InstrumentType.EQUITY
    )

    client = PublicApiClient(
        ApiKeyAuthConfig(api_secret_key=secret),
        config=PublicApiClientConfiguration(default_account_number=account_id),
    )
    try:
        now_utc = _now_utc()
        symbol_price, quote_ts = _get_quote_price(client, now_utc, symbol=symbol, instrument_type=instrument_type)
        exp_targets = _resolve_expiration_targets(client, symbol=symbol, instrument_type=instrument_type)
        if not exp_targets:
            raise HTTPException(status_code=502, detail=f"No {symbol} expirations")
        expiration = _pick_expiration(exp_targets, expiry_mode=expiry_mode, dte=dte)
        expiration, by_strike, chain_ts = _get_chain_data(
            client,
            now_utc,
            symbol=symbol,
            instrument_type=instrument_type,
            expiration=expiration,
        )
        days_to_expiry = _days_to_expiry(expiration)
        strike_window_size = _strike_window_size(days_to_expiry)
        strikes = _windowed_strikes(by_strike, symbol_price, atm_strikes=strike_window_size)
        ts_iso = _iso_utc(now_utc)

        # Append full-chain slim snapshot for analytics.
        full_rows = [by_strike[s] for s in sorted(by_strike.keys())]
        slim = [{"strike": s["strike"], "put_vol": s.get("put_vol"), "call_vol": s.get("call_vol")} for s in full_rows]
        snapshot_buffer = _snapshot_buffers.setdefault((symbol, expiry_mode, dte), deque(maxlen=512))
        snapshot_buffer.append((ts_iso, slim))
        _prune_buffer(snapshot_buffer, now_utc)

        em = _compute_expected_move(by_strike, symbol_price) or {}
        hot_calls, hot_puts = _compute_hot_strikes(
            full_rows, snapshot_buffer=snapshot_buffer, target_minutes=5, top_n=HOT_STRIKES_TOP_N
        )
        spread_scanner = _compute_spread_scanner(by_strike, symbol_price)

        return {
            "symbol": symbol,
            "dte": dte,
            "expiry_mode": expiry_mode,
            "expiration": expiration,
            "days_to_expiry": days_to_expiry,
            "strike_window_size": strike_window_size,
            "expirations": exp_targets,
            "symbol_price": symbol_price,
            "spx_price": symbol_price,
            "timestamp": ts_iso,
            "quote_timestamp": quote_ts,
            "chain_timestamp": chain_ts,
            "quote_refresh_seconds": QUOTE_REFRESH_SECONDS,
            "chain_refresh_seconds": CHAIN_REFRESH_SECONDS,
            "strikes": strikes,
            **em,
            "hot_strikes_call": hot_calls,
            "hot_strikes_put": hot_puts,
            "spread_scanner": spread_scanner,
        }
    finally:
        client.close()


@app.get("/api/snapshot")
def get_snapshot(mark_last_min: int | None = None, dte: int = 0, symbol: str = DEFAULT_SYMBOL, expiry_mode: str = "dte"):
    result = _fetch_snapshot(symbol=symbol, dte=dte, expiry_mode=expiry_mode)
    snapshot_buffer = _snapshot_buffers.setdefault((result["symbol"], result["expiry_mode"], dte), deque(maxlen=512))
    if mark_last_min is not None and mark_last_min > 0 and snapshot_buffer:
        now_utc = datetime.utcnow()
        target = now_utc - timedelta(minutes=mark_last_min)
        candidates = [(ts_iso, slim) for (ts_iso, slim) in snapshot_buffer if ts_iso != result["timestamp"]]
        if not candidates:
            for s in result["strikes"]:
                s["delta_put"] = None
                s["delta_call"] = None
        else:
            def _dist(item):
                ts_iso, _ = item
                ts_dt = datetime.fromisoformat(ts_iso.replace("Z", ""))
                return abs((ts_dt - target).total_seconds())
            best = min(candidates, key=_dist)
            old_slim = best[1]
            old_by_strike = {s["strike"]: s for s in old_slim}
            for s in result["strikes"]:
                old = old_by_strike.get(s["strike"], {})
                s["delta_put"] = (s.get("put_vol") or 0) - (old.get("put_vol") or 0)
                s["delta_call"] = (s.get("call_vol") or 0) - (old.get("call_vol") or 0)
    return result


# Serve static frontend if built
_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(_dist, "assets")), name="assets")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(_dist, "index.html"))

    @app.get("/{path:path}")
    def catch_all(path: str):
        """SPA fallback: serve index.html for non-API routes."""
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(os.path.join(_dist, "index.html"))
