"""
FastAPI server for the options-chain dashboard.
Serves GET /api/snapshot (chain + quote) and static frontend from ../frontend/dist.
Run from repo root: uvicorn web.server.main:app --reload
"""
import os
import sys
import math
from collections import deque
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from copy import deepcopy
from zoneinfo import ZoneInfo

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

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from supabase import create_client as create_supabase_client
except ImportError:
    create_supabase_client = None

app = FastAPI(title="options-chain API")

DEFAULT_SYMBOL = "SPX"
STRADDLE_MONITOR_SYMBOL = "SPX"
SUPPORTED_SYMBOLS = {
    "SPX": "INDEX",
    "NDX": "INDEX",
    "SPY": "EQUITY",
    "QQQ": "EQUITY",
    "NVDA": "EQUITY",
    "TSLA": "EQUITY",
    "AAPL": "EQUITY",
    "MSFT": "EQUITY",
    "GOOGL": "EQUITY",
    "META": "EQUITY",
    "AMZN": "EQUITY",
    "IBIT": "EQUITY",
    "AVGO": "EQUITY",
}
DEFAULT_STRIKE_DEPTH = 25  # default ATM ± N strikes shown in the main table
MAX_STRIKE_DEPTH = 100
QUOTE_REFRESH_SECONDS = 10
CHAIN_REFRESH_SECONDS = 60
SNAPSHOT_BUFFER_MAX_AGE_MINUTES = 5
HOT_STRIKES_TOP_N = 8
SKEW_GREEKS_WINDOW_STRIKES = 30
SKEW_MIN_COVERAGE_WARN_PCT = 60.0
GREEKS_FETCH_CHUNK_SIZE = 100
ATR_PERIOD = 14
ATR_HISTORY_LOOKBACK_SESSIONS = 80
ATR_MEMORY_CACHE_SECONDS = 15 * 60
STRADDLE_MONITOR_DEFAULT_ROWS = 8
STRADDLE_MONITOR_MAX_ROWS = 12
STRADDLE_MONITOR_HISTORY_TABLE = "straddle_monitor_intraday"
STRADDLE_MONITOR_DAILY_CLOSE_TABLE = "straddle_monitor_daily_close"
STRADDLE_MONITOR_HISTORY_DTES = (0, 1)
STRADDLE_MONITOR_DAILY_CLOSE_SESSIONS = 5
STRADDLE_CLOSE_CAPTURE_WINDOW_MINUTES = 15
STRADDLE_MONITOR_RESPONSE_CACHE_SECONDS = 15
MARKET_TIMEZONE = ZoneInfo("America/New_York")
MARKET_OPEN_TIME = time(9, 30)
MARKET_CLOSE_TIME = time(16, 0)
SUPPORTED_DTES = {0, 1}
SUPPORTED_EXPIRY_MODES = {"dte", "friday"}
SUPPORTED_EXPIRY_SLOTS = {"0dte", "next1", "next2"}
ATR_SOURCE_SYMBOL_OVERRIDES = {
    "SPX": "^GSPC",
    "NDX": "^NDX",
}
_snapshot_buffers = {}  # (symbol, expiration) -> deque[(iso_ts, strikes_slim: [{strike, put_vol, call_vol}])]
_quote_cache_by_symbol = {}  # symbol -> {fetched_at, quote_snapshot}
_chain_cache_by_symbol_exp = {}  # (symbol, expiration) -> {fetched_at, by_strike, timestamp}
_greeks_cache_by_symbol_exp = {}  # (symbol, expiration) -> {fetched_at, by_osi, timestamp}
_atr_cache_by_symbol = {}  # symbol -> {fetched_at, analysis}
_supabase_client_cache = None
_straddle_monitor_response_cache = {}  # row_limit -> {fetched_at, payload}


def _norm_exp(exp):
    if hasattr(exp, "strftime"):
        return exp.strftime("%Y-%m-%d")
    return str(exp)


def _iso_or_none(exp_date):
    return exp_date.isoformat() if exp_date else None


def _allow_same_day_0dte(symbol: str, today: date) -> bool:
    if symbol == "SPX":
        return today.weekday() <= 4
    return today.weekday() in {0, 2, 4}


def _build_expiry_slots(normalized_dates: list[date], today: date, symbol: str):
    """Build slot-based expirations used by the new dashboard UI."""
    date_set = set(normalized_dates)
    slot_0dte = today if today in date_set and _allow_same_day_0dte(symbol, today) else None
    future_dates = [exp for exp in normalized_dates if exp > today]
    slot_next1 = future_dates[0] if len(future_dates) > 0 else None
    slot_next2 = future_dates[1] if len(future_dates) > 1 else None
    return {
        "slot_0dte": _iso_or_none(slot_0dte),
        "slot_next1": _iso_or_none(slot_next1),
        "slot_next2": _iso_or_none(slot_next2),
    }


def _build_legacy_expiration_targets(normalized_dates: list[date], today: date):
    """Keep legacy expiry fields for backward compatibility."""
    if not normalized_dates:
        return {"dte0": None, "dte1": None, "friday": None}

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


def _resolve_requested_expiry_slot(expiry_slot: str | None, expiry_mode: str, dte: int):
    if expiry_slot is not None:
        slot = str(expiry_slot).strip().lower()
        if slot not in SUPPORTED_EXPIRY_SLOTS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported expiry_slot={expiry_slot}; expected one of {sorted(SUPPORTED_EXPIRY_SLOTS)}",
            )
        return slot
    if expiry_mode == "friday":
        return "next2"
    return "next1" if dte == 1 else "0dte"


def _resolve_expiration_for_slot(exp_targets: dict[str, str | None], requested_slot: str):
    if requested_slot == "0dte":
        slot_candidates = ("0dte", "next1", "next2")
    elif requested_slot == "next1":
        slot_candidates = ("next1", "next2")
    else:
        slot_candidates = ("next2", "next1")

    for slot in slot_candidates:
        expiration = exp_targets.get(f"slot_{slot}")
        if expiration:
            return slot, expiration
    return None, None


def _match_slot_for_expiration(exp_targets: dict[str, str | None], expiration: str | None):
    if not expiration:
        return None
    for slot in ("0dte", "next1", "next2"):
        if exp_targets.get(f"slot_{slot}") == expiration:
            return slot
    return None


def _resolve_expiration_targets(client, symbol: str, instrument_type):
    """Resolve both slot-based and legacy expiration targets used by the UI."""
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
    slot_targets = _build_expiry_slots(normalized_dates, today=today, symbol=symbol)
    legacy_targets = _build_legacy_expiration_targets(normalized_dates, today=today)
    return {**legacy_targets, **slot_targets}


def _pick_expiration(exp_targets: dict[str, str | None], expiry_mode: str, dte: int):
    if expiry_mode == "friday":
        return exp_targets.get("friday")
    return exp_targets.get("dte1") if dte == 1 else exp_targets.get("dte0")


def _coerce_row_limit(row_limit, default: int = STRADDLE_MONITOR_DEFAULT_ROWS, maximum: int = STRADDLE_MONITOR_MAX_ROWS):
    if row_limit is None:
        return default
    try:
        value = int(str(row_limit).strip())
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return min(value, maximum)


def _as_utc(ts: datetime):
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _market_session_bounds(now_utc: datetime):
    now_et = _as_utc(now_utc).astimezone(MARKET_TIMEZONE)
    session_date = now_et.date()
    session_start = datetime.combine(session_date, MARKET_OPEN_TIME, tzinfo=MARKET_TIMEZONE).astimezone(timezone.utc)
    session_end = datetime.combine(session_date, MARKET_CLOSE_TIME, tzinfo=MARKET_TIMEZONE).astimezone(timezone.utc)
    return session_start, session_end


def _market_session_date(now_utc: datetime):
    return _as_utc(now_utc).astimezone(MARKET_TIMEZONE).date()


def _is_regular_market_hours(now_utc: datetime):
    now_et = _as_utc(now_utc).astimezone(MARKET_TIMEZONE)
    if now_et.weekday() > 4:
        return False
    open_dt = datetime.combine(now_et.date(), MARKET_OPEN_TIME, tzinfo=MARKET_TIMEZONE)
    close_dt = datetime.combine(now_et.date(), MARKET_CLOSE_TIME, tzinfo=MARKET_TIMEZONE)
    return open_dt <= now_et < close_dt


def _floor_to_minute_utc(now_utc: datetime):
    return _as_utc(now_utc).replace(second=0, microsecond=0)


def _is_straddle_close_capture_window(now_utc: datetime, window_minutes: int = STRADDLE_CLOSE_CAPTURE_WINDOW_MINUTES):
    now_et = _as_utc(now_utc).astimezone(MARKET_TIMEZONE)
    if now_et.weekday() > 4:
        return False
    close_dt = datetime.combine(now_et.date(), MARKET_CLOSE_TIME, tzinfo=MARKET_TIMEZONE)
    capture_end = close_dt + timedelta(minutes=max(1, int(window_minutes)))
    return close_dt <= now_et < capture_end


def _monitor_expirations_from_dates(normalized_dates: list[date], today: date, symbol: str, row_limit: int):
    if not normalized_dates:
        return []
    available = []
    date_set = set(normalized_dates)
    if today in date_set and _allow_same_day_0dte(symbol, today):
        available.append(today)
    available.extend(exp for exp in normalized_dates if exp > today)
    return [exp.isoformat() for exp in available[:row_limit]]


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


def _normal_cdf(z: float):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


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
                "call_osi": None,
                "put_osi": None,
            },
        )
        by_strike[strike]["call_oi"] = getattr(opt, "open_interest", None)
        by_strike[strike]["call_vol"] = getattr(opt, "volume", None)
        by_strike[strike]["call_bid"] = _decimal_float(getattr(opt, "bid", None))
        by_strike[strike]["call_ask"] = _decimal_float(getattr(opt, "ask", None))
        by_strike[strike]["call_osi"] = osi

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
                "call_osi": None,
                "put_osi": None,
            },
        )
        by_strike[strike]["put_oi"] = getattr(opt, "open_interest", None)
        by_strike[strike]["put_vol"] = getattr(opt, "volume", None)
        by_strike[strike]["put_bid"] = _decimal_float(getattr(opt, "bid", None))
        by_strike[strike]["put_ask"] = _decimal_float(getattr(opt, "ask", None))
        by_strike[strike]["put_osi"] = osi

    return by_strike


def _days_to_expiry(expiration: str):
    try:
        exp_date = date.fromisoformat(expiration)
    except (TypeError, ValueError):
        return None
    return max(0, (exp_date - date.today()).days)


def _resolve_strike_depth(strike_depth):
    if strike_depth is None:
        return DEFAULT_STRIKE_DEPTH
    try:
        value = int(str(strike_depth).strip())
    except (TypeError, ValueError):
        return DEFAULT_STRIKE_DEPTH
    if value <= 0:
        return DEFAULT_STRIKE_DEPTH
    return min(value, MAX_STRIKE_DEPTH)


def _windowed_strikes(by_strike, spx_price, atm_strikes: int = DEFAULT_STRIKE_DEPTH):
    strikes_list = sorted(by_strike.keys(), reverse=True)
    if spx_price is None or not strikes_list:
        return [by_strike[s] for s in strikes_list]
    atm_idx = min(range(len(strikes_list)), key=lambda i: abs(strikes_list[i] - spx_price))
    lo = max(0, atm_idx - atm_strikes)
    hi = min(len(strikes_list), atm_idx + atm_strikes + 1)
    return [by_strike[s] for s in strikes_list[lo:hi]]


def _get_quote_snapshot(client, now_utc: datetime, symbol: str, instrument_type):
    cache_entry = _quote_cache_by_symbol.setdefault(symbol, {"fetched_at": None, "quote_snapshot": None})
    fetched_at = cache_entry.get("fetched_at")
    if fetched_at is not None and (now_utc - fetched_at).total_seconds() < QUOTE_REFRESH_SECONDS:
        cached = cache_entry.get("quote_snapshot") or {}
        return dict(cached)
    quotes = client.get_quotes([OrderInstrument(symbol=symbol, type=instrument_type)])
    symbol_price = None
    day_high = None
    day_low = None
    prev_close = None
    if quotes and len(quotes) > 0:
        quote = quotes[0]
        symbol_price = _decimal_float(getattr(quote, "last", None))
        day_high = _decimal_float(getattr(quote, "high", None))
        day_low = _decimal_float(getattr(quote, "low", None))
        prev_close = _decimal_float(getattr(quote, "close", None))
    ts = _iso_utc(now_utc)
    quote_snapshot = {
        "last": symbol_price,
        "high": day_high,
        "low": day_low,
        "close": prev_close,
        "timestamp": ts,
    }
    cache_entry["fetched_at"] = now_utc
    cache_entry["quote_snapshot"] = quote_snapshot
    return dict(quote_snapshot)


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


def _atr_source_symbol(symbol: str):
    return ATR_SOURCE_SYMBOL_OVERRIDES.get(symbol, symbol)


def _get_supabase_secret_key():
    return os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")


def _get_supabase_client():
    global _supabase_client_cache
    if _supabase_client_cache is not None:
        return _supabase_client_cache
    if create_supabase_client is None:
        return None
    supabase_url = (os.getenv("SUPABASE_URL") or "").strip()
    supabase_secret = (_get_supabase_secret_key() or "").strip()
    if not supabase_url or not supabase_secret:
        return None
    try:
        _supabase_client_cache = create_supabase_client(supabase_url, supabase_secret)
    except Exception:
        return None
    return _supabase_client_cache


def _supabase_get_cached_atr_row(symbol: str, session_date: str):
    if not session_date:
        return None
    client = _get_supabase_client()
    if client is None:
        return None
    try:
        response = (
            client.table("atr_cache_daily")
            .select("*")
            .eq("symbol", symbol)
            .eq("session_date", session_date)
            .limit(1)
            .execute()
        )
        rows = getattr(response, "data", None) or []
        return rows[0] if rows else None
    except Exception:
        return None


def _supabase_get_recent_atr_rows(symbol: str, limit: int = 5):
    client = _get_supabase_client()
    if client is None:
        return []
    try:
        response = (
            client.table("atr_cache_daily")
            .select("*")
            .eq("symbol", symbol)
            .order("session_date", desc=True)
            .limit(limit)
            .execute()
        )
        return getattr(response, "data", None) or []
    except Exception:
        return []


def _supabase_upsert_atr_row(row_payload: dict):
    client = _get_supabase_client()
    if client is None:
        return False
    try:
        client.table("atr_cache_daily").upsert(row_payload, on_conflict="symbol,session_date").execute()
        return True
    except Exception:
        return False


def _supabase_upsert_straddle_history_rows(rows_payload: list[dict]):
    if not rows_payload:
        return False
    client = _get_supabase_client()
    if client is None:
        return False
    try:
        client.table(STRADDLE_MONITOR_HISTORY_TABLE).upsert(
            rows_payload,
            on_conflict="symbol,expiration,bucket_ts",
        ).execute()
        return True
    except Exception:
        return False


def _supabase_upsert_straddle_daily_close_rows(rows_payload: list[dict]):
    if not rows_payload:
        return False
    client = _get_supabase_client()
    if client is None:
        return False
    try:
        client.table(STRADDLE_MONITOR_DAILY_CLOSE_TABLE).upsert(
            rows_payload,
            on_conflict="symbol,session_date,expiration",
        ).execute()
        return True
    except Exception:
        return False


def _supabase_get_straddle_history_rows(symbol: str, session_start_iso: str):
    client = _get_supabase_client()
    if client is None:
        return []
    try:
        response = (
            client.table(STRADDLE_MONITOR_HISTORY_TABLE)
            .select("*")
            .eq("symbol", symbol)
            .gte("bucket_ts", session_start_iso)
            .order("bucket_ts")
            .execute()
        )
        return getattr(response, "data", None) or []
    except Exception:
        return []


def _supabase_get_straddle_daily_close_rows(symbol: str, row_limit: int):
    client = _get_supabase_client()
    if client is None:
        return []
    try:
        response = (
            client.table(STRADDLE_MONITOR_DAILY_CLOSE_TABLE)
            .select("*")
            .eq("symbol", symbol)
            .order("session_date", desc=True)
            .order("expiration")
            .limit(row_limit)
            .execute()
        )
        return getattr(response, "data", None) or []
    except Exception:
        return []


def _fetch_daily_history_rows(source_symbol: str, now_utc: datetime, lookback_sessions: int = ATR_HISTORY_LOOKBACK_SESSIONS):
    if yf is None:
        return []
    try:
        history = yf.Ticker(source_symbol).history(period="1y", interval="1d", auto_adjust=False, actions=False)
    except Exception:
        return []
    if history is None or history.empty:
        return []

    today = now_utc.date()
    rows = []
    for idx, bar in history.iterrows():
        session_date = idx.date() if hasattr(idx, "date") else None
        if session_date is None or session_date >= today:
            continue
        high = _decimal_float(bar.get("High"))
        low = _decimal_float(bar.get("Low"))
        close = _decimal_float(bar.get("Close"))
        if high is None or low is None or close is None:
            continue
        rows.append(
            {
                "date": session_date,
                "high": high,
                "low": low,
                "close": close,
            }
        )

    if len(rows) > lookback_sessions + ATR_PERIOD + 10:
        rows = rows[-(lookback_sessions + ATR_PERIOD + 10):]
    return rows


def _compute_wilder_atr_from_rows(rows: list[dict], period: int = ATR_PERIOD):
    if not rows or len(rows) < (period + 1):
        return None, None

    true_ranges = []
    for idx in range(1, len(rows)):
        current = rows[idx]
        prev_close = _decimal_float(rows[idx - 1].get("close"))
        high = _decimal_float(current.get("high"))
        low = _decimal_float(current.get("low"))
        if prev_close is None or high is None or low is None:
            continue
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None, None

    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = ((atr * (period - 1)) + tr) / period

    asof_date = rows[-1].get("date")
    asof_session = asof_date.isoformat() if hasattr(asof_date, "isoformat") else None
    return atr, asof_session


def _build_atr_unavailable(source_symbol: str, message: str):
    return {
        "status": "unavailable",
        "method": "wilder_atr14_completed_sessions",
        "source": "yfinance",
        "source_symbol": source_symbol,
        "asof_session": None,
        "previous_close": None,
        "atr14": None,
        "plus_1atr_level": None,
        "minus_1atr_level": None,
        "plus_2atr_level": None,
        "minus_2atr_level": None,
        "message": message,
    }


def _atr_analysis_from_cache_row(row: dict):
    previous_close = _decimal_float(row.get("previous_close"))
    atr14 = _decimal_float(row.get("atr14"))
    if None in (previous_close, atr14):
        return None
    plus_1atr_level = _decimal_float(row.get("plus_1atr_level"))
    minus_1atr_level = _decimal_float(row.get("minus_1atr_level"))
    if plus_1atr_level is None:
        plus_1atr_level = previous_close + atr14
    if minus_1atr_level is None:
        minus_1atr_level = previous_close - atr14
    plus_2atr_level = previous_close + (2.0 * atr14)
    minus_2atr_level = previous_close - (2.0 * atr14)
    return {
        "status": "ok",
        "method": "wilder_atr14_completed_sessions",
        "source": "yfinance",
        "source_symbol": row.get("source_symbol"),
        "asof_session": row.get("session_date"),
        "previous_close": round(previous_close, 2),
        "atr14": round(atr14, 2),
        "plus_1atr_level": round(plus_1atr_level, 2),
        "minus_1atr_level": round(minus_1atr_level, 2),
        "plus_2atr_level": round(plus_2atr_level, 2),
        "minus_2atr_level": round(minus_2atr_level, 2),
    }


def _pick_cached_atr_row(rows: list[dict], previous_close):
    if not rows:
        return None
    target_close = _decimal_float(previous_close)
    if target_close is None:
        return rows[0]
    best_row = None
    best_gap = None
    for row in rows:
        row_close = _decimal_float(row.get("previous_close"))
        if row_close is None:
            continue
        gap = abs(row_close - target_close)
        if best_gap is None or gap < best_gap:
            best_row = row
            best_gap = gap
    if best_row is not None and best_gap is not None and best_gap <= 0.02:
        return best_row
    return None


def _atr_memory_cache_get(symbol: str, now_utc: datetime, previous_close):
    entry = _atr_cache_by_symbol.get(symbol)
    if not entry:
        return None
    fetched_at = entry.get("fetched_at")
    if fetched_at is None or (now_utc - fetched_at).total_seconds() > ATR_MEMORY_CACHE_SECONDS:
        return None
    analysis = entry.get("analysis")
    if not analysis:
        return None
    target_close = _decimal_float(previous_close)
    cached_close = _decimal_float(analysis.get("previous_close"))
    if target_close is not None and cached_close is not None and abs(target_close - cached_close) > 0.02:
        return None
    return deepcopy(analysis)


def _atr_memory_cache_set(symbol: str, now_utc: datetime, analysis: dict):
    _atr_cache_by_symbol[symbol] = {
        "fetched_at": now_utc,
        "analysis": deepcopy(analysis),
    }


def _compute_atr_analysis(symbol: str, quote_snapshot: dict, now_utc: datetime):
    source_symbol = _atr_source_symbol(symbol)
    previous_close = _decimal_float((quote_snapshot or {}).get("close"))

    cached_memory = _atr_memory_cache_get(symbol, now_utc, previous_close)
    if cached_memory is not None:
        return cached_memory

    recent_rows = _supabase_get_recent_atr_rows(symbol=symbol, limit=5)
    cached_row = _pick_cached_atr_row(recent_rows, previous_close)
    if cached_row:
        cached_analysis = _atr_analysis_from_cache_row(cached_row)
        if cached_analysis is not None:
            _atr_memory_cache_set(symbol, now_utc, cached_analysis)
            return cached_analysis

    history_rows = _fetch_daily_history_rows(source_symbol=source_symbol, now_utc=now_utc)
    if not history_rows:
        analysis = _build_atr_unavailable(source_symbol, "Daily history unavailable.")
        _atr_memory_cache_set(symbol, now_utc, analysis)
        return analysis

    atr14, asof_session = _compute_wilder_atr_from_rows(history_rows, period=ATR_PERIOD)
    if atr14 is None or asof_session is None:
        analysis = _build_atr_unavailable(source_symbol, "Insufficient history for ATR(14).")
        _atr_memory_cache_set(symbol, now_utc, analysis)
        return analysis

    existing_row = _supabase_get_cached_atr_row(symbol=symbol, session_date=asof_session)
    if existing_row:
        existing_analysis = _atr_analysis_from_cache_row(existing_row)
        if existing_analysis is not None:
            _atr_memory_cache_set(symbol, now_utc, existing_analysis)
            return existing_analysis

    if previous_close is None:
        previous_close = _decimal_float(history_rows[-1].get("close"))
    if previous_close is None:
        analysis = _build_atr_unavailable(source_symbol, "Previous close unavailable.")
        _atr_memory_cache_set(symbol, now_utc, analysis)
        return analysis

    plus_1atr = previous_close + atr14
    minus_1atr = previous_close - atr14
    plus_2atr = previous_close + (2.0 * atr14)
    minus_2atr = previous_close - (2.0 * atr14)
    analysis = {
        "status": "ok",
        "method": "wilder_atr14_completed_sessions",
        "source": "yfinance",
        "source_symbol": source_symbol,
        "asof_session": asof_session,
        "previous_close": round(previous_close, 2),
        "atr14": round(atr14, 2),
        "plus_1atr_level": round(plus_1atr, 2),
        "minus_1atr_level": round(minus_1atr, 2),
        "plus_2atr_level": round(plus_2atr, 2),
        "minus_2atr_level": round(minus_2atr, 2),
    }
    _atr_memory_cache_set(symbol, now_utc, analysis)

    _supabase_upsert_atr_row(
        {
            "symbol": symbol,
            "session_date": asof_session,
            "source_symbol": source_symbol,
            "previous_close": round(previous_close, 4),
            "atr14": round(atr14, 4),
            "plus_1atr_level": round(plus_1atr, 4),
            "minus_1atr_level": round(minus_1atr, 4),
            "computed_at": _iso_utc(now_utc),
        }
    )

    return analysis


def _spread_deterministic_key(spread):
    short_strike = _decimal_float(spread.get("short_strike"))
    long_strike = _decimal_float(spread.get("long_strike"))
    short_token = f"{short_strike:.3f}" if short_strike is not None else "~"
    long_token = f"{long_strike:.3f}" if long_strike is not None else "~"
    return f"{short_token}|{long_token}"


def _pick_atr_target_spread(spreads, target_level):
    level = _decimal_float(target_level)
    if level is None:
        return None
    if not spreads:
        return None

    def _sort_key(spread):
        short_strike = _decimal_float(spread.get("short_strike"))
        gap = abs(short_strike - level) if short_strike is not None else float("inf")
        mark_credit = _decimal_float(spread.get("mark_credit")) or 0.0
        distance = _decimal_float(spread.get("distance_from_spx"))
        distance_key = distance if distance is not None else float("inf")
        return (
            gap,
            -mark_credit,
            distance_key,
            _spread_deterministic_key(spread),
        )

    best = min(spreads, key=_sort_key)
    best_short = _decimal_float(best.get("short_strike"))
    gap = abs(best_short - level) if best_short is not None else None
    selected = dict(best)
    selected["atr_target_level"] = round(level, 2)
    selected["atr_gap"] = round(gap, 2) if gap is not None else None
    return selected


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


def _compute_bwb_scanner(by_strike, spx_price, greeks_by_osi):
    empty = {
        "call_bwb_credit_spreads": [],
        "put_bwb_credit_spreads": [],
    }
    if spx_price is None or not by_strike:
        return empty

    strikes = sorted(by_strike.keys())
    if len(strikes) < 4:
        return empty

    def _leg_data(strike, side):
        row = by_strike.get(strike, {})
        if side == "call":
            return (
                _decimal_float(row.get("call_bid")),
                _decimal_float(row.get("call_ask")),
                row.get("call_osi"),
                row.get("call_vol"),
                row.get("call_oi"),
            )
        return (
            _decimal_float(row.get("put_bid")),
            _decimal_float(row.get("put_ask")),
            row.get("put_osi"),
            row.get("put_vol"),
            row.get("put_oi"),
        )

    def _bwb_entry(side, low_strike, mid_strike, high_strike):
        low_bid, low_ask, _low_osi, _low_vol, _low_oi = _leg_data(low_strike, side)
        mid_bid, mid_ask, mid_osi, mid_vol, mid_oi = _leg_data(mid_strike, side)
        high_bid, high_ask, _high_osi, _high_vol, _high_oi = _leg_data(high_strike, side)
        if None in (low_bid, low_ask, mid_bid, mid_ask, high_bid, high_ask):
            return None

        low_mid = _mid(low_bid, low_ask)
        mid_mid = _mid(mid_bid, mid_ask)
        high_mid = _mid(high_bid, high_ask)
        if None in (low_mid, mid_mid, high_mid):
            return None

        bid_credit = round((2.0 * mid_bid) - low_ask - high_ask, 2)
        ask_credit = round((2.0 * mid_ask) - low_bid - high_bid, 2)
        mark_credit = round((2.0 * mid_mid) - low_mid - high_mid, 2)
        if mark_credit <= 0:
            return None

        if side == "call":
            narrow_wing_width = round(mid_strike - low_strike, 2)
            broken_wing_width = round(high_strike - mid_strike, 2)
            breakeven = round(mid_strike + narrow_wing_width + mark_credit, 2)
        else:
            narrow_wing_width = round(high_strike - mid_strike, 2)
            broken_wing_width = round(mid_strike - low_strike, 2)
            breakeven = round(mid_strike - narrow_wing_width - mark_credit, 2)
        if narrow_wing_width <= 0 or broken_wing_width <= narrow_wing_width:
            return None

        max_loss = round(broken_wing_width - narrow_wing_width - mark_credit, 2)
        if max_loss <= 0:
            return None
        max_profit = round(narrow_wing_width + mark_credit, 2)
        rom_pct = round((mark_credit / max_loss) * 100.0, 1)

        body_delta = None
        pop_delta_pct = None
        if mid_osi:
            greek = greeks_by_osi.get(mid_osi, {}) if greeks_by_osi else {}
            body_delta = _decimal_float(greek.get("delta"))
            if body_delta is not None:
                pop_delta = max(0.0, min(1.0, 1.0 - abs(body_delta)))
                pop_delta_pct = round(pop_delta * 100.0, 1)
                body_delta = round(body_delta, 4)

        return {
            "side": side,
            "low_strike": low_strike,
            "mid_strike": mid_strike,
            "high_strike": high_strike,
            "narrow_wing_width": narrow_wing_width,
            "broken_wing_width": broken_wing_width,
            "distance_from_spx": round(abs(mid_strike - spx_price), 2),
            "bid_credit": bid_credit,
            "ask_credit": ask_credit,
            "mark_credit": mark_credit,
            "max_loss": max_loss,
            "max_profit": max_profit,
            "rom_pct": rom_pct,
            "breakeven": breakeven,
            "body_delta": body_delta,
            "pop_delta_pct": pop_delta_pct,
            "body_volume": mid_vol,
            "body_oi": mid_oi,
        }

    call_bwbs = []
    put_bwbs = []

    # Put BWB: +1 high / -2 mid / +1 low (wider downside wing), all strikes below spot.
    for mid_idx in range(1, len(strikes) - 1):
        mid = strikes[mid_idx]
        high = strikes[mid_idx + 1]
        narrow_width = high - mid
        if narrow_width <= 0:
            continue
        low_idx = None
        for cand_idx in range(mid_idx - 1, -1, -1):
            if (mid - strikes[cand_idx]) > narrow_width:
                low_idx = cand_idx
                break
        if low_idx is None:
            continue
        low = strikes[low_idx]
        if not (low < spx_price and mid < spx_price and high < spx_price):
            continue
        entry = _bwb_entry("put", low, mid, high)
        if entry:
            put_bwbs.append(entry)

    # Call BWB: +1 low / -2 mid / +1 high (wider upside wing), all strikes above spot.
    for mid_idx in range(1, len(strikes) - 1):
        low = strikes[mid_idx - 1]
        mid = strikes[mid_idx]
        narrow_width = mid - low
        if narrow_width <= 0:
            continue
        high_idx = None
        for cand_idx in range(mid_idx + 1, len(strikes)):
            if (strikes[cand_idx] - mid) > narrow_width:
                high_idx = cand_idx
                break
        if high_idx is None:
            continue
        high = strikes[high_idx]
        if not (low > spx_price and mid > spx_price and high > spx_price):
            continue
        entry = _bwb_entry("call", low, mid, high)
        if entry:
            call_bwbs.append(entry)

    def _sort_key(row):
        return (
            -(_decimal_float(row.get("rom_pct")) or 0.0),
            -(_decimal_float(row.get("distance_from_spx")) or 0.0),
            -(_decimal_float(row.get("mark_credit")) or 0.0),
        )

    call_bwbs.sort(key=_sort_key)
    put_bwbs.sort(key=_sort_key)
    return {
        "call_bwb_credit_spreads": call_bwbs,
        "put_bwb_credit_spreads": put_bwbs,
    }


def _chunk_symbols(symbols: list[str], chunk_size: int):
    size = max(1, int(chunk_size or 1))
    for i in range(0, len(symbols), size):
        yield symbols[i:i + size]


def _parse_greeks_response_by_osi(response):
    parsed = {}
    for greek_data in getattr(response, "greeks", []) or []:
        osi = getattr(greek_data, "osi_symbol", None) or getattr(greek_data, "symbol", None)
        if not osi:
            continue
        greeks_obj = getattr(greek_data, "greeks", None)
        if greeks_obj is None:
            parsed[osi] = {"delta": None, "implied_volatility": None}
            continue
        parsed[osi] = {
            "delta": _decimal_float(getattr(greeks_obj, "delta", None)),
            "implied_volatility": _decimal_float(getattr(greeks_obj, "implied_volatility", None)),
        }
    return parsed


def _get_option_greeks_map(client, now_utc: datetime, symbol: str, expiration: str, osi_symbols: list[str]):
    symbols = sorted({s for s in osi_symbols if s})
    if not symbols:
        return {}
    cache_key = (symbol, expiration)
    cache_entry = _greeks_cache_by_symbol_exp.get(cache_key)
    cached_by_osi = cache_entry.get("by_osi") if cache_entry else {}
    cached_by_osi = cached_by_osi or {}
    if cache_entry:
        fetched_at = cache_entry.get("fetched_at")
        if (
            fetched_at is not None
            and (now_utc - fetched_at).total_seconds() < CHAIN_REFRESH_SECONDS
            and all(sym in cached_by_osi for sym in symbols)
        ):
            return {sym: cached_by_osi.get(sym, {}) for sym in symbols}

    by_osi = dict(cached_by_osi)
    successful_fetch = False
    for chunk in _chunk_symbols(symbols, GREEKS_FETCH_CHUNK_SIZE):
        try:
            response = client.get_option_greeks(osi_symbols=chunk)
        except Exception:
            continue
        successful_fetch = True
        by_osi.update(_parse_greeks_response_by_osi(response))

    if successful_fetch:
        _greeks_cache_by_symbol_exp[cache_key] = {
            "fetched_at": now_utc,
            "by_osi": by_osi,
            "timestamp": _iso_utc(now_utc),
        }
    return {sym: by_osi.get(sym, {}) for sym in symbols}


def _select_skew_osi_symbols(
    by_strike,
    symbol_price,
    window_strikes: int = SKEW_GREEKS_WINDOW_STRIKES,
):
    strikes = sorted(by_strike.keys())
    if not strikes:
        return []
    spot = _decimal_float(symbol_price)
    if spot is None:
        atm_idx = len(strikes) // 2
    else:
        atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    depth = max(1, int(window_strikes))
    lo = max(0, atm_idx - depth)
    hi = min(len(strikes), atm_idx + depth + 1)
    symbols = []
    for strike in strikes[lo:hi]:
        row = by_strike.get(strike, {})
        if row.get("call_osi"):
            symbols.append(row.get("call_osi"))
        if row.get("put_osi"):
            symbols.append(row.get("put_osi"))
    return sorted({s for s in symbols if s})


def _round_or_none(value, digits=4):
    if value is None:
        return None
    return round(value, digits)


def _skew_node_payload(strike=None, delta=None, iv=None):
    return {
        "strike": _round_or_none(_decimal_float(strike), 2),
        "delta": _round_or_none(_decimal_float(delta), 4),
        "iv": _round_or_none(_decimal_float(iv), 4),
    }


def _select_delta_node(candidates, target_delta: float, spot: float | None, side: str):
    if not candidates:
        return _skew_node_payload()

    def _sort_key(candidate):
        strike = _decimal_float(candidate.get("strike"))
        delta = _decimal_float(candidate.get("delta"))
        if delta is None:
            delta_dist = float("inf")
        else:
            delta_dist = abs(delta - target_delta)

        if spot is not None and spot > 0 and strike is not None and strike > 0:
            expected_side = strike < spot if side == "put" else strike > spot
            side_penalty = 0 if expected_side else 1
            moneyness_dist = abs(math.log(strike / spot))
        else:
            side_penalty = 0
            moneyness_dist = float("inf")

        strike_key = strike if strike is not None else float("inf")
        return (delta_dist, side_penalty, moneyness_dist, strike_key)

    best = min(candidates, key=_sort_key)
    return _skew_node_payload(best.get("strike"), best.get("delta"), best.get("iv"))


def _compute_skew_analysis(
    by_strike,
    greeks_by_osi,
    symbol_price,
    symbol: str,
    expiration: str,
    days_to_expiry: int | None,
    requested_osi_symbols: list[str] | None = None,
):
    spot = _decimal_float(symbol_price)
    if spot is None or spot <= 0:
        return {
            "status": "unavailable",
            "method": "delta_iv_nodes",
            "symbol": symbol,
            "expiration": expiration,
            "days_to_expiry": days_to_expiry,
            "spot": spot,
            "nodes": {
                "put_10d": _skew_node_payload(),
                "put_25d": _skew_node_payload(),
                "atm_50d": _skew_node_payload(),
                "call_25d": _skew_node_payload(),
                "call_10d": _skew_node_payload(),
            },
            "metrics": {
                "atm_iv": None,
                "rr_25": None,
                "bf_25": None,
                "put_call_iv_ratio_25": None,
                "put_wing_slope_25_atm": None,
                "call_wing_slope_25_atm": None,
                "slope_asymmetry": None,
            },
            "diagnostics": {
                "available_nodes": [],
                "missing_nodes": ["put_10d", "put_25d", "atm_50d", "call_25d", "call_10d"],
                "greeks_coverage_pct": None,
                "warnings": ["Spot price unavailable; cannot compute skew nodes."],
            },
        }

    call_candidates = []
    put_candidates = []
    strikes_list = sorted(by_strike.keys())

    for strike in strikes_list:
        row = by_strike.get(strike, {})
        for side in ("call", "put"):
            osi_key = "call_osi" if side == "call" else "put_osi"
            osi = row.get(osi_key)
            if not osi:
                continue
            greek = greeks_by_osi.get(osi, {})
            delta = _decimal_float(greek.get("delta"))
            iv = _decimal_float(greek.get("implied_volatility"))
            if delta is None:
                continue
            candidate = {
                "strike": _decimal_float(strike),
                "delta": delta,
                "iv": iv,
            }
            if side == "call":
                call_candidates.append(candidate)
            else:
                put_candidates.append(candidate)

    coverage_pct = None
    requested_symbols = sorted({s for s in (requested_osi_symbols or []) if s})
    if requested_symbols:
        covered_osi = 0
        for osi in requested_symbols:
            greek = greeks_by_osi.get(osi, {})
            delta = _decimal_float(greek.get("delta"))
            iv = _decimal_float(greek.get("implied_volatility"))
            if delta is not None and iv is not None:
                covered_osi += 1
        coverage_pct = round((covered_osi / len(requested_symbols)) * 100.0, 1)

    put_10d = _select_delta_node(put_candidates, -0.10, spot, "put")
    put_25d = _select_delta_node(put_candidates, -0.25, spot, "put")
    call_25d = _select_delta_node(call_candidates, 0.25, spot, "call")
    call_10d = _select_delta_node(call_candidates, 0.10, spot, "call")

    atm_strike = min(strikes_list, key=lambda s: abs(s - spot)) if strikes_list else None
    atm_50d = _skew_node_payload()
    if atm_strike is not None:
        atm_row = by_strike.get(atm_strike, {})
        call_greek = greeks_by_osi.get(atm_row.get("call_osi"), {}) if atm_row.get("call_osi") else {}
        put_greek = greeks_by_osi.get(atm_row.get("put_osi"), {}) if atm_row.get("put_osi") else {}
        call_iv = _decimal_float(call_greek.get("implied_volatility"))
        put_iv = _decimal_float(put_greek.get("implied_volatility"))
        call_delta = _decimal_float(call_greek.get("delta"))
        put_delta = _decimal_float(put_greek.get("delta"))

        if call_iv is not None and put_iv is not None:
            atm_iv = (call_iv + put_iv) / 2.0
            atm_delta = call_delta if call_delta is not None else put_delta
        elif call_iv is not None:
            atm_iv = call_iv
            atm_delta = call_delta
        elif put_iv is not None:
            atm_iv = put_iv
            atm_delta = put_delta
        else:
            atm_iv = None
            atm_delta = call_delta if call_delta is not None else put_delta
        atm_50d = _skew_node_payload(atm_strike, atm_delta, atm_iv)

    nodes = {
        "put_10d": put_10d,
        "put_25d": put_25d,
        "atm_50d": atm_50d,
        "call_25d": call_25d,
        "call_10d": call_10d,
    }

    iv_put_25 = _decimal_float(put_25d.get("iv"))
    iv_call_25 = _decimal_float(call_25d.get("iv"))
    iv_atm = _decimal_float(atm_50d.get("iv"))
    k_put_25 = _decimal_float(put_25d.get("strike"))
    k_call_25 = _decimal_float(call_25d.get("strike"))

    rr_25 = None
    if iv_call_25 is not None and iv_put_25 is not None:
        rr_25 = iv_call_25 - iv_put_25

    bf_25 = None
    if iv_call_25 is not None and iv_put_25 is not None and iv_atm is not None:
        bf_25 = 0.5 * (iv_call_25 + iv_put_25) - iv_atm

    put_call_iv_ratio_25 = None
    if iv_put_25 is not None and iv_call_25 is not None and iv_call_25 > 0:
        put_call_iv_ratio_25 = iv_put_25 / iv_call_25

    put_wing_slope = None
    if (
        iv_put_25 is not None
        and iv_atm is not None
        and k_put_25 is not None
        and spot > 0
        and k_put_25 > 0
    ):
        denom_put = math.log(k_put_25 / spot)
        if abs(denom_put) > 1e-12:
            put_wing_slope = (iv_put_25 - iv_atm) / denom_put

    call_wing_slope = None
    if (
        iv_call_25 is not None
        and iv_atm is not None
        and k_call_25 is not None
        and spot > 0
        and k_call_25 > 0
    ):
        denom_call = math.log(k_call_25 / spot)
        if abs(denom_call) > 1e-12:
            call_wing_slope = (iv_call_25 - iv_atm) / denom_call

    slope_asymmetry = None
    if put_wing_slope is not None and call_wing_slope is not None:
        slope_asymmetry = put_wing_slope - call_wing_slope

    available_nodes = [name for name, node in nodes.items() if node.get("iv") is not None]
    missing_nodes = [name for name, node in nodes.items() if node.get("iv") is None]
    warnings = []
    if missing_nodes:
        warnings.append(f"Missing IV for nodes: {', '.join(missing_nodes)}")
    if coverage_pct is not None and coverage_pct < SKEW_MIN_COVERAGE_WARN_PCT:
        warnings.append("Low greeks coverage; skew metrics may be noisy.")

    core_nodes = ("put_25d", "atm_50d", "call_25d")
    if not available_nodes:
        status = "unavailable"
    elif all(nodes[name].get("iv") is not None for name in core_nodes):
        status = "ok"
    else:
        status = "partial"

    return {
        "status": status,
        "method": "delta_iv_nodes",
        "symbol": symbol,
        "expiration": expiration,
        "days_to_expiry": days_to_expiry,
        "spot": _round_or_none(spot, 2),
        "nodes": nodes,
        "metrics": {
            "atm_iv": _round_or_none(iv_atm, 4),
            "rr_25": _round_or_none(rr_25, 4),
            "bf_25": _round_or_none(bf_25, 4),
            "put_call_iv_ratio_25": _round_or_none(put_call_iv_ratio_25, 4),
            "put_wing_slope_25_atm": _round_or_none(put_wing_slope, 6),
            "call_wing_slope_25_atm": _round_or_none(call_wing_slope, 6),
            "slope_asymmetry": _round_or_none(slope_asymmetry, 6),
        },
        "diagnostics": {
            "available_nodes": available_nodes,
            "missing_nodes": missing_nodes,
            "greeks_coverage_pct": coverage_pct,
            "warnings": warnings,
        },
    }


def _resolve_monitor_expirations(client, symbol: str, instrument_type, row_limit: int):
    expirations = get_option_expirations(client, symbol, instrument_type=instrument_type)
    if not expirations:
        return []
    parsed = []
    for exp in expirations:
        exp_str = _norm_exp(exp)
        try:
            parsed.append(date.fromisoformat(exp_str))
        except ValueError:
            continue
    if not parsed:
        return []
    normalized_dates = sorted(set(parsed))
    return _monitor_expirations_from_dates(
        normalized_dates=normalized_dates,
        today=date.today(),
        symbol=symbol,
        row_limit=row_limit,
    )


def _select_nearest_strike_row(by_strike, symbol_price):
    strikes_list = sorted(by_strike.keys())
    if not strikes_list:
        return None, {}
    if symbol_price is None:
        strike = strikes_list[len(strikes_list) // 2]
    else:
        strike = min(strikes_list, key=lambda s: abs(s - symbol_price))
    return strike, by_strike.get(strike, {})


def _quote_change_fields(quote_snapshot: dict):
    last = _decimal_float((quote_snapshot or {}).get("last"))
    prev_close = _decimal_float((quote_snapshot or {}).get("close"))
    change = None
    change_pct = None
    if last is not None and prev_close is not None:
        change = round(last - prev_close, 2)
        if prev_close:
            change_pct = round(change / prev_close, 6)
    return last, change, change_pct


def _build_straddle_monitor_row(by_strike, greeks_by_osi, symbol_price, expiration: str):
    days_to_expiry = _days_to_expiry(expiration)
    strike, strike_row = _select_nearest_strike_row(by_strike, symbol_price)

    call_bid = _decimal_float(strike_row.get("call_bid"))
    call_ask = _decimal_float(strike_row.get("call_ask"))
    put_bid = _decimal_float(strike_row.get("put_bid"))
    put_ask = _decimal_float(strike_row.get("put_ask"))
    call_mid = _mid(call_bid, call_ask)
    put_mid = _mid(put_bid, put_ask)
    straddle_mid = round(call_mid + put_mid, 2) if call_mid is not None and put_mid is not None else None
    implied_move_pct = None
    if straddle_mid is not None and symbol_price is not None and symbol_price > 0:
        implied_move_pct = round(straddle_mid / symbol_price, 6)

    call_greek = greeks_by_osi.get(strike_row.get("call_osi"), {}) if strike_row.get("call_osi") else {}
    put_greek = greeks_by_osi.get(strike_row.get("put_osi"), {}) if strike_row.get("put_osi") else {}
    call_iv = _decimal_float(call_greek.get("implied_volatility"))
    put_iv = _decimal_float(put_greek.get("implied_volatility"))

    row_iv = None
    if call_iv is not None and put_iv is not None:
        row_iv = (call_iv + put_iv) / 2.0
    elif call_iv is not None:
        row_iv = call_iv
    elif put_iv is not None:
        row_iv = put_iv

    put_call_skew = None
    if put_iv is not None and call_iv is not None and call_iv > 0:
        put_call_skew = put_iv / call_iv

    return {
        "days_to_expiry": days_to_expiry,
        "expiration": expiration,
        "strike": _round_or_none(strike, 2),
        "call_bid": _round_or_none(call_bid, 2),
        "call_ask": _round_or_none(call_ask, 2),
        "put_bid": _round_or_none(put_bid, 2),
        "put_ask": _round_or_none(put_ask, 2),
        "call_mid": _round_or_none(call_mid, 2),
        "put_mid": _round_or_none(put_mid, 2),
        "straddle_mid": _round_or_none(straddle_mid, 2),
        "implied_move_points": _round_or_none(straddle_mid, 2),
        "implied_move_pct": _round_or_none(implied_move_pct, 6),
        "put_call_skew": _round_or_none(put_call_skew, 4),
        "iv": _round_or_none(row_iv, 4),
    }


def _build_straddle_history_write_payload(row: dict, symbol: str, spot: float | None, bucket_ts: datetime):
    if row.get("days_to_expiry") not in STRADDLE_MONITOR_HISTORY_DTES:
        return None
    straddle_mid = _decimal_float(row.get("straddle_mid"))
    strike = _decimal_float(row.get("strike"))
    if straddle_mid is None or strike is None or spot is None:
        return None
    return {
        "symbol": symbol,
        "expiration": row.get("expiration"),
        "days_to_expiry": row.get("days_to_expiry"),
        "strike": round(strike, 2),
        "spot": round(spot, 2),
        "straddle_mid": round(straddle_mid, 2),
        "implied_move_pct": _round_or_none(_decimal_float(row.get("implied_move_pct")), 6),
        "bucket_ts": bucket_ts.isoformat(),
    }


def _build_straddle_daily_close_write_payload(
    row: dict,
    symbol: str,
    spot: float | None,
    session_date: date,
    captured_at: datetime,
):
    straddle_mid = _decimal_float(row.get("straddle_mid"))
    strike = _decimal_float(row.get("strike"))
    expiration = row.get("expiration")
    if straddle_mid is None or strike is None or spot is None or not expiration:
        return None
    return {
        "symbol": symbol,
        "session_date": session_date.isoformat(),
        "captured_at": _as_utc(captured_at).isoformat(),
        "expiration": expiration,
        "days_to_expiry": row.get("days_to_expiry"),
        "strike": round(strike, 2),
        "spot": round(spot, 2),
        "straddle_mid": round(straddle_mid, 2),
        "implied_move_pct": _round_or_none(_decimal_float(row.get("implied_move_pct")), 6),
        "put_call_skew": _round_or_none(_decimal_float(row.get("put_call_skew")), 4),
        "iv": _round_or_none(_decimal_float(row.get("iv")), 4),
    }


def _shape_straddle_history(rows: list[dict]):
    history = {"0dte": [], "1dte": []}
    for row in rows:
        dte = row.get("days_to_expiry")
        if dte not in STRADDLE_MONITOR_HISTORY_DTES:
            continue
        key = "0dte" if dte == 0 else "1dte"
        point_ts = row.get("bucket_ts") or row.get("recorded_at")
        straddle_mid = _decimal_float(row.get("straddle_mid"))
        if point_ts is None or straddle_mid is None:
            continue
        history[key].append(
            {
                "timestamp": point_ts,
                "value": round(straddle_mid, 2),
                "spot": _round_or_none(_decimal_float(row.get("spot")), 2),
                "strike": _round_or_none(_decimal_float(row.get("strike")), 2),
                "expiration": row.get("expiration"),
            }
        )
    return history


def _shape_straddle_daily_close_history(
    rows: list[dict],
    session_limit: int = STRADDLE_MONITOR_DAILY_CLOSE_SESSIONS,
):
    history = []
    seen_sessions = set()
    for row in rows:
        session_date = row.get("session_date")
        if not session_date:
            continue
        if session_date not in seen_sessions:
            if len(seen_sessions) >= session_limit:
                break
            seen_sessions.add(session_date)
        history.append(
            {
                "session_date": session_date,
                "captured_at": row.get("captured_at"),
                "expiration": row.get("expiration"),
                "days_to_expiry": row.get("days_to_expiry"),
                "strike": _round_or_none(_decimal_float(row.get("strike")), 2),
                "spot": _round_or_none(_decimal_float(row.get("spot")), 2),
                "straddle_mid": _round_or_none(_decimal_float(row.get("straddle_mid")), 2),
                "implied_move_pct": _round_or_none(_decimal_float(row.get("implied_move_pct")), 6),
                "put_call_skew": _round_or_none(_decimal_float(row.get("put_call_skew")), 4),
                "iv": _round_or_none(_decimal_float(row.get("iv")), 4),
            }
        )
    return history


def _straddle_monitor_cache_get(row_limit: int, now_utc: datetime):
    entry = _straddle_monitor_response_cache.get(row_limit)
    if not entry:
        return None
    fetched_at = entry.get("fetched_at")
    if fetched_at is None:
        return None
    if (now_utc - fetched_at).total_seconds() > STRADDLE_MONITOR_RESPONSE_CACHE_SECONDS:
        return None
    payload = entry.get("payload")
    if not payload:
        return None
    return deepcopy(payload)


def _straddle_monitor_cache_set(row_limit: int, now_utc: datetime, payload: dict):
    _straddle_monitor_response_cache[row_limit] = {
        "fetched_at": now_utc,
        "payload": deepcopy(payload),
    }


def _create_public_api_client():
    secret = get_api_secret()
    account_id = get_account_id()
    if not secret:
        raise RuntimeError("PUBLIC_COM_SECRET not set")
    if PublicApiClient is None:
        raise RuntimeError("publicdotcom-py not installed")
    account_id = _resolve_account_id(secret=secret, account_id=account_id)
    return PublicApiClient(
        ApiKeyAuthConfig(api_secret_key=secret),
        config=PublicApiClientConfiguration(default_account_number=account_id),
    )


def _build_straddle_monitor_snapshot(client, now_utc: datetime, row_limit: int):
    spx_quote = _get_quote_snapshot(client, now_utc, symbol=STRADDLE_MONITOR_SYMBOL, instrument_type=InstrumentType.INDEX)
    vix_quote = _get_quote_snapshot(client, now_utc, symbol="VIX", instrument_type=InstrumentType.INDEX)

    expirations = _resolve_monitor_expirations(
        client,
        symbol=STRADDLE_MONITOR_SYMBOL,
        instrument_type=InstrumentType.INDEX,
        row_limit=row_limit,
    )
    if not expirations:
        raise HTTPException(status_code=502, detail="No SPX expirations")

    spot, spot_change, spot_change_pct = _quote_change_fields(spx_quote)
    vix, vix_change, vix_change_pct = _quote_change_fields(vix_quote)

    rows = []
    history_writes = []
    bucket_ts = _floor_to_minute_utc(now_utc)
    latest_chain_ts = None

    for expiration in expirations:
        _, by_strike, chain_ts = _get_chain_data(
            client,
            now_utc,
            symbol=STRADDLE_MONITOR_SYMBOL,
            instrument_type=InstrumentType.INDEX,
            expiration=expiration,
        )
        latest_chain_ts = chain_ts
        _, strike_row = _select_nearest_strike_row(by_strike, spot)
        osi_symbols = [sym for sym in (strike_row.get("call_osi"), strike_row.get("put_osi")) if sym]
        greeks_by_osi = _get_option_greeks_map(
            client,
            now_utc,
            symbol=STRADDLE_MONITOR_SYMBOL,
            expiration=expiration,
            osi_symbols=osi_symbols,
        )
        row = _build_straddle_monitor_row(
            by_strike=by_strike,
            greeks_by_osi=greeks_by_osi,
            symbol_price=spot,
            expiration=expiration,
        )
        rows.append(row)
        write_payload = _build_straddle_history_write_payload(
            row=row,
            symbol=STRADDLE_MONITOR_SYMBOL,
            spot=spot,
            bucket_ts=bucket_ts,
        )
        if write_payload is not None:
            history_writes.append(write_payload)

    active_strike = rows[0].get("strike") if rows else None
    return {
        "symbol": STRADDLE_MONITOR_SYMBOL,
        "spot": _round_or_none(spot, 2),
        "spot_change": _round_or_none(spot_change, 2),
        "spot_change_pct": _round_or_none(spot_change_pct, 6),
        "vix": _round_or_none(vix, 2),
        "vix_change": _round_or_none(vix_change, 2),
        "vix_change_pct": _round_or_none(vix_change_pct, 6),
        "active_strike": active_strike,
        "updated_at": _iso_utc(now_utc),
        "quote_timestamp": spx_quote.get("timestamp"),
        "vix_timestamp": vix_quote.get("timestamp"),
        "chain_timestamp": latest_chain_ts,
        "rows": rows,
        "history_writes": history_writes,
    }


def _fetch_straddle_monitor(row_limit=None):
    row_limit = _coerce_row_limit(row_limit)
    now_utc = _now_utc()
    cached = _straddle_monitor_cache_get(row_limit=row_limit, now_utc=now_utc)
    if cached is not None:
        return cached
    try:
        client = _create_public_api_client()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    try:
        snapshot = _build_straddle_monitor_snapshot(client=client, now_utc=now_utc, row_limit=row_limit)

        if _is_regular_market_hours(now_utc):
            _supabase_upsert_straddle_history_rows(snapshot.pop("history_writes", []))
        else:
            snapshot.pop("history_writes", None)

        session_start_utc, _ = _market_session_bounds(now_utc)
        history_rows = _supabase_get_straddle_history_rows(
            symbol=STRADDLE_MONITOR_SYMBOL,
            session_start_iso=session_start_utc.isoformat(),
        )
        close_rows = _supabase_get_straddle_daily_close_rows(
            symbol=STRADDLE_MONITOR_SYMBOL,
            row_limit=STRADDLE_MONITOR_DAILY_CLOSE_SESSIONS * STRADDLE_MONITOR_MAX_ROWS,
        )
        payload = {
            **snapshot,
            "history": _shape_straddle_history(history_rows),
            "daily_closes": _shape_straddle_daily_close_history(close_rows),
            "history_resolution_seconds": 60,
            "daily_close_capture_time": "16:00 ET",
        }
        _straddle_monitor_cache_set(row_limit=row_limit, now_utc=now_utc, payload=payload)
        return payload
    finally:
        client.close()


def _capture_straddle_daily_close_snapshot(row_limit=None, now_utc: datetime | None = None, force: bool = False):
    now_utc = _as_utc(now_utc or _now_utc())
    row_limit = _coerce_row_limit(row_limit)
    if not force and not _is_straddle_close_capture_window(now_utc):
        return {
            "status": "skipped",
            "reason": "outside_capture_window",
            "session_date": _market_session_date(now_utc).isoformat(),
            "captured_at": now_utc.isoformat(),
            "rows_persisted": 0,
        }

    try:
        client = _create_public_api_client()
    except RuntimeError as exc:
        raise RuntimeError(str(exc))

    try:
        snapshot = _build_straddle_monitor_snapshot(client=client, now_utc=now_utc, row_limit=row_limit)
    finally:
        client.close()

    session_date = _market_session_date(now_utc)
    rows_payload = []
    for row in snapshot.get("rows", []):
        payload = _build_straddle_daily_close_write_payload(
            row=row,
            symbol=STRADDLE_MONITOR_SYMBOL,
            spot=_decimal_float(snapshot.get("spot")),
            session_date=session_date,
            captured_at=now_utc,
        )
        if payload is not None:
            rows_payload.append(payload)

    if not rows_payload:
        raise RuntimeError("No straddle rows available to persist for the close snapshot.")
    if not _supabase_upsert_straddle_daily_close_rows(rows_payload):
        raise RuntimeError("Supabase daily close upsert failed.")

    return {
        "status": "captured",
        "session_date": session_date.isoformat(),
        "captured_at": now_utc.isoformat(),
        "rows_persisted": len(rows_payload),
        "expirations": [row.get("expiration") for row in rows_payload if row.get("expiration")],
    }


def _attach_pop_to_spreads(spreads, side: str, by_strike, greeks_by_osi, symbol_price, days_to_expiry):
    t_years = max(days_to_expiry or 0, 1) / 365.0
    sqrt_t = math.sqrt(t_years)
    s0 = _decimal_float(symbol_price)
    for spread in spreads:
        short_strike = _decimal_float(spread.get("short_strike"))
        credit = _decimal_float(spread.get("mark_credit"))
        short_row = by_strike.get(short_strike, {})
        short_osi = short_row.get("call_osi") if side == "call" else short_row.get("put_osi")
        greek = greeks_by_osi.get(short_osi, {}) if short_osi else {}
        delta = _decimal_float(greek.get("delta"))
        iv = _decimal_float(greek.get("implied_volatility"))

        pop = None
        pop_method = "unavailable"
        if short_strike is not None and credit is not None:
            breakeven = short_strike + credit if side == "call" else short_strike - credit
            if iv is not None and iv > 0 and s0 is not None and s0 > 0 and breakeven > 0 and sqrt_t > 0:
                z = (math.log(breakeven / s0) + 0.5 * (iv ** 2) * t_years) / (iv * sqrt_t)
                phi = _normal_cdf(z)
                pop = phi if side == "call" else 1.0 - phi
                pop_method = "breakeven_iv"
            elif delta is not None:
                pop = 1.0 - abs(delta)
                pop_method = "delta_fallback"
        if pop is not None:
            pop = max(0.0, min(1.0, pop))
            spread["pop"] = round(pop, 4)
            spread["pop_pct"] = round(pop * 100.0, 1)
        else:
            spread["pop"] = None
            spread["pop_pct"] = None
        spread["pop_method"] = pop_method

        pop_delta = None
        pop_delta_method = "unavailable"
        if delta is not None:
            pop_delta = max(0.0, min(1.0, 1.0 - abs(delta)))
            pop_delta_method = "delta_direct"
        if pop_delta is not None:
            spread["pop_delta"] = round(pop_delta, 4)
            spread["pop_delta_pct"] = round(pop_delta * 100.0, 1)
        else:
            spread["pop_delta"] = None
            spread["pop_delta_pct"] = None
        spread["pop_delta_method"] = pop_delta_method


def _attach_pop_to_bwbs(spreads, side: str, by_strike, greeks_by_osi):
    for spread in spreads:
        mid_strike = _decimal_float(spread.get("mid_strike"))
        mid_row = by_strike.get(mid_strike, {})
        mid_osi = mid_row.get("call_osi") if side == "call" else mid_row.get("put_osi")
        greek = greeks_by_osi.get(mid_osi, {}) if mid_osi else {}
        delta = _decimal_float(greek.get("delta"))

        if delta is None:
            spread["body_delta"] = None
            spread["pop_delta"] = None
            spread["pop_delta_pct"] = None
            spread["pop_delta_method"] = "unavailable"
            continue

        pop_delta = max(0.0, min(1.0, 1.0 - abs(delta)))
        spread["body_delta"] = round(delta, 4)
        spread["pop_delta"] = round(pop_delta, 4)
        spread["pop_delta_pct"] = round(pop_delta * 100.0, 1)
        spread["pop_delta_method"] = "delta_direct"


def _collect_spread_osi_symbols(spread_scanner, by_strike):
    symbols = []

    def _append_vertical(rows, side):
        for spread in rows:
            short_strike = _decimal_float(spread.get("short_strike"))
            row = by_strike.get(short_strike, {})
            osi = row.get("call_osi") if side == "call" else row.get("put_osi")
            if osi:
                symbols.append(osi)

    def _append_bwb(rows, side):
        for spread in rows:
            mid_strike = _decimal_float(spread.get("mid_strike"))
            row = by_strike.get(mid_strike, {})
            osi = row.get("call_osi") if side == "call" else row.get("put_osi")
            if osi:
                symbols.append(osi)

    _append_vertical(spread_scanner.get("call_credit_spreads", []), "call")
    _append_vertical(spread_scanner.get("put_credit_spreads", []), "put")
    _append_bwb(spread_scanner.get("call_bwb_credit_spreads", []), "call")
    _append_bwb(spread_scanner.get("put_bwb_credit_spreads", []), "put")
    return sorted({s for s in symbols if s})


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


def _fetch_snapshot(
    symbol: str = DEFAULT_SYMBOL,
    dte: int = 0,
    expiry_mode: str = "dte",
    expiry_slot: str | None = None,
    strike_depth=None,
    include_atr: bool = False,
    include_skew: bool = False,
):
    expiry_mode = expiry_mode.lower()
    if expiry_slot is None:
        if dte not in SUPPORTED_DTES:
            raise HTTPException(status_code=400, detail=f"Unsupported dte={dte}; expected one of {sorted(SUPPORTED_DTES)}")
        if expiry_mode not in SUPPORTED_EXPIRY_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported expiry_mode={expiry_mode}; expected one of {sorted(SUPPORTED_EXPIRY_MODES)}",
            )
    expiry_slot_requested = _resolve_requested_expiry_slot(expiry_slot=expiry_slot, expiry_mode=expiry_mode, dte=dte)

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
        quote_snapshot = _get_quote_snapshot(client, now_utc, symbol=symbol, instrument_type=instrument_type)
        symbol_price = _decimal_float(quote_snapshot.get("last"))
        quote_ts = quote_snapshot.get("timestamp")
        exp_targets = _resolve_expiration_targets(client, symbol=symbol, instrument_type=instrument_type)
        if not exp_targets:
            raise HTTPException(status_code=502, detail=f"No {symbol} expirations")
        if expiry_slot is not None:
            expiry_slot_resolved, expiration = _resolve_expiration_for_slot(exp_targets, requested_slot=expiry_slot_requested)
        else:
            expiration = _pick_expiration(exp_targets, expiry_mode=expiry_mode, dte=dte)
            expiry_slot_resolved = _match_slot_for_expiration(exp_targets, expiration) or expiry_slot_requested

        if not expiration:
            raise HTTPException(
                status_code=502,
                detail=f"No usable expiration for {symbol}; requested_slot={expiry_slot_requested}",
            )

        expiration, by_strike, chain_ts = _get_chain_data(
            client,
            now_utc,
            symbol=symbol,
            instrument_type=instrument_type,
            expiration=expiration,
        )
        days_to_expiry = _days_to_expiry(expiration)
        strike_window_size = _resolve_strike_depth(strike_depth)
        strikes = _windowed_strikes(by_strike, symbol_price, atm_strikes=strike_window_size)
        ts_iso = _iso_utc(now_utc)

        # Append full-chain slim snapshot for analytics.
        full_rows = [by_strike[s] for s in sorted(by_strike.keys())]
        slim = [{"strike": s["strike"], "put_vol": s.get("put_vol"), "call_vol": s.get("call_vol")} for s in full_rows]
        snapshot_buffer = _snapshot_buffers.setdefault((symbol, expiration), deque(maxlen=512))
        snapshot_buffer.append((ts_iso, slim))
        _prune_buffer(snapshot_buffer, now_utc)

        em = _compute_expected_move(by_strike, symbol_price) or {}
        hot_calls, hot_puts = _compute_hot_strikes(
            full_rows, snapshot_buffer=snapshot_buffer, target_minutes=5, top_n=HOT_STRIKES_TOP_N
        )
        spread_scanner = _compute_spread_scanner(by_strike, symbol_price)
        spread_scanner.update(_compute_bwb_scanner(by_strike, symbol_price, {}))
        spread_osi_symbols = _collect_spread_osi_symbols(spread_scanner, by_strike)
        greeks_by_osi = _get_option_greeks_map(
            client,
            now_utc,
            symbol=symbol,
            expiration=expiration,
            osi_symbols=spread_osi_symbols,
        )
        _attach_pop_to_spreads(
            spread_scanner.get("call_credit_spreads", []),
            side="call",
            by_strike=by_strike,
            greeks_by_osi=greeks_by_osi,
            symbol_price=symbol_price,
            days_to_expiry=days_to_expiry,
        )
        _attach_pop_to_spreads(
            spread_scanner.get("put_credit_spreads", []),
            side="put",
            by_strike=by_strike,
            greeks_by_osi=greeks_by_osi,
            symbol_price=symbol_price,
            days_to_expiry=days_to_expiry,
        )
        _attach_pop_to_bwbs(
            spread_scanner.get("call_bwb_credit_spreads", []),
            side="call",
            by_strike=by_strike,
            greeks_by_osi=greeks_by_osi,
        )
        _attach_pop_to_bwbs(
            spread_scanner.get("put_bwb_credit_spreads", []),
            side="put",
            by_strike=by_strike,
            greeks_by_osi=greeks_by_osi,
        )
        atr_analysis = None
        atr_target_spreads = {}
        if include_atr:
            atr_analysis = _compute_atr_analysis(symbol=symbol, quote_snapshot=quote_snapshot, now_utc=now_utc)
            atr_target_spreads = {
                "call_plus_1atr": None,
                "put_minus_1atr": None,
                "call_plus_2atr": None,
                "put_minus_2atr": None,
            }
            if atr_analysis.get("status") == "ok":
                atr_target_spreads["call_plus_1atr"] = _pick_atr_target_spread(
                    spread_scanner.get("call_credit_spreads", []),
                    atr_analysis.get("plus_1atr_level"),
                )
                atr_target_spreads["put_minus_1atr"] = _pick_atr_target_spread(
                    spread_scanner.get("put_credit_spreads", []),
                    atr_analysis.get("minus_1atr_level"),
                )
                atr_target_spreads["call_plus_2atr"] = _pick_atr_target_spread(
                    spread_scanner.get("call_credit_spreads", []),
                    atr_analysis.get("plus_2atr_level"),
                )
                atr_target_spreads["put_minus_2atr"] = _pick_atr_target_spread(
                    spread_scanner.get("put_credit_spreads", []),
                    atr_analysis.get("minus_2atr_level"),
                )

        skew_analysis = None
        if include_skew:
            skew_osi_symbols = _select_skew_osi_symbols(
                by_strike,
                symbol_price,
                window_strikes=SKEW_GREEKS_WINDOW_STRIKES,
            )
            skew_greeks_by_osi = _get_option_greeks_map(
                client,
                now_utc,
                symbol=symbol,
                expiration=expiration,
                osi_symbols=skew_osi_symbols,
            )
            skew_analysis = _compute_skew_analysis(
                by_strike=by_strike,
                greeks_by_osi=skew_greeks_by_osi,
                symbol_price=symbol_price,
                symbol=symbol,
                expiration=expiration,
                days_to_expiry=days_to_expiry,
                requested_osi_symbols=skew_osi_symbols,
            )

        return {
            "symbol": symbol,
            "dte": dte,
            "expiry_mode": expiry_mode,
            "expiry_slot_requested": expiry_slot_requested,
            "expiry_slot_resolved": expiry_slot_resolved,
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
            "atr_analysis": atr_analysis,
            "atr_target_spreads": atr_target_spreads,
            "skew_analysis": skew_analysis,
        }
    finally:
        client.close()


@app.get("/api/snapshot")
def get_snapshot(
    mark_last_min: int | None = None,
    dte: int = 0,
    symbol: str = DEFAULT_SYMBOL,
    expiry_mode: str = "dte",
    expiry_slot: str | None = None,
    strike_depth: str | None = None,
    include_atr: bool = False,
    include_skew: bool = False,
):
    result = _fetch_snapshot(
        symbol=symbol,
        dte=dte,
        expiry_mode=expiry_mode,
        expiry_slot=expiry_slot,
        strike_depth=strike_depth,
        include_atr=include_atr,
        include_skew=include_skew,
    )
    snapshot_buffer = _snapshot_buffers.setdefault((result["symbol"], result["expiration"]), deque(maxlen=512))
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


@app.get("/api/straddle-monitor")
def get_straddle_monitor(rows: int | None = None):
    return _fetch_straddle_monitor(row_limit=rows)


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
