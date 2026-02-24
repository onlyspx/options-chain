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
        OptionExpirationsRequest,
    )
    from public_api_sdk.auth_config import ApiKeyAuthConfig
except ImportError:
    PublicApiClient = None

app = FastAPI(title="SPX 0DTE Dashboard API")

SYMBOL = "SPX"
ATM_STRIKES = 15  # show ATM ± N strikes
SNAPSHOT_BUFFER_MAX_AGE_MINUTES = 20
_snapshot_buffer = deque(maxlen=128)  # (iso_ts, strikes_slim: list of {strike, put_vol, call_vol})


def _norm_exp(exp):
    if hasattr(exp, "strftime"):
        return exp.strftime("%Y-%m-%d")
    return str(exp)


def _resolve_expiration(client):
    """SPX expiration: same-day if available, else nearest."""
    expirations = get_option_expirations(client, SYMBOL, instrument_type=InstrumentType.INDEX)
    if not expirations:
        return None
    today_str = date.today().isoformat()
    for exp in expirations:
        if _norm_exp(exp) == today_str:
            return _norm_exp(exp)
    return _norm_exp(expirations[0])


def _decimal_float(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    return None


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


def _fetch_snapshot():
    secret = get_api_secret()
    account_id = get_account_id()
    if not secret:
        raise HTTPException(status_code=500, detail="PUBLIC_COM_SECRET not set")
    if PublicApiClient is None:
        raise HTTPException(status_code=500, detail="publicdotcom-py not installed")
    account_id = _resolve_account_id(secret=secret, account_id=account_id)

    client = PublicApiClient(
        ApiKeyAuthConfig(api_secret_key=secret),
        config=PublicApiClientConfiguration(default_account_number=account_id),
    )
    try:
        # SPX quote (underlying price)
        quotes = client.get_quotes([
            OrderInstrument(symbol=SYMBOL, type=InstrumentType.INDEX),
        ])
        spx_price = None
        if quotes and len(quotes) > 0:
            spx_price = _decimal_float(getattr(quotes[0], "last", None))

        expiration = _resolve_expiration(client)
        if not expiration:
            raise HTTPException(status_code=502, detail="No SPX expirations")

        request = OptionChainRequest(
            instrument=OrderInstrument(symbol=SYMBOL, type=InstrumentType.INDEX),
            expiration_date=expiration,
        )
        chain = client.get_option_chain(request)
        calls = getattr(chain, "calls", []) or []
        puts = getattr(chain, "puts", []) or []

        # Merge by strike: strike -> { call_*, put_* }
        by_strike = {}
        for opt in calls:
            osi = opt.instrument.symbol if hasattr(opt, "instrument") else ""
            strike = parse_osi_symbol(osi)
            if strike is None:
                continue
            by_strike.setdefault(strike, {
                "strike": strike,
                "call_oi": None, "put_oi": None,
                "call_vol": None, "put_vol": None,
                "call_bid": None, "call_ask": None,
                "put_bid": None, "put_ask": None,
            })
            by_strike[strike]["call_oi"] = getattr(opt, "open_interest", None)
            by_strike[strike]["call_vol"] = getattr(opt, "volume", None)
            by_strike[strike]["call_bid"] = _decimal_float(getattr(opt, "bid", None))
            by_strike[strike]["call_ask"] = _decimal_float(getattr(opt, "ask", None))

        for opt in puts:
            osi = opt.instrument.symbol if hasattr(opt, "instrument") else ""
            strike = parse_osi_symbol(osi)
            if strike is None:
                continue
            by_strike.setdefault(strike, {
                "strike": strike,
                "call_oi": None, "put_oi": None,
                "call_vol": None, "put_vol": None,
                "call_bid": None, "call_ask": None,
                "put_bid": None, "put_ask": None,
            })
            by_strike[strike]["put_oi"] = getattr(opt, "open_interest", None)
            by_strike[strike]["put_vol"] = getattr(opt, "volume", None)
            by_strike[strike]["put_bid"] = _decimal_float(getattr(opt, "bid", None))
            by_strike[strike]["put_ask"] = _decimal_float(getattr(opt, "ask", None))

        strikes_list = sorted(by_strike.keys(), reverse=True)  # highest strike first
        if spx_price is not None and strikes_list:
            # Restrict to ATM ± ATM_STRIKES
            atm_idx = min(range(len(strikes_list)), key=lambda i: abs(strikes_list[i] - spx_price))
            lo = max(0, atm_idx - ATM_STRIKES)
            hi = min(len(strikes_list), atm_idx + ATM_STRIKES + 1)
            strikes_list = strikes_list[lo:hi]
        strikes = [by_strike[s] for s in strikes_list]

        now_utc = datetime.utcnow()
        ts_iso = now_utc.isoformat() + "Z"

        # Append to rolling buffer (slim: strike, put_vol, call_vol only)
        slim = [{"strike": s["strike"], "put_vol": s.get("put_vol"), "call_vol": s.get("call_vol")} for s in strikes]
        _snapshot_buffer.append((ts_iso, slim))
        # Prune older than SNAPSHOT_BUFFER_MAX_AGE_MINUTES (naive UTC)
        cutoff = now_utc - timedelta(minutes=SNAPSHOT_BUFFER_MAX_AGE_MINUTES)
        while _snapshot_buffer:
            first_ts = datetime.fromisoformat(_snapshot_buffer[0][0].replace("Z", ""))
            if first_ts < cutoff:
                _snapshot_buffer.popleft()
            else:
                break

        return {
            "expiration": expiration,
            "spx_price": spx_price,
            "timestamp": ts_iso,
            "strikes": strikes,
        }
    finally:
        client.close()


@app.get("/api/snapshot")
def get_snapshot(mark_last_min: int | None = None):
    result = _fetch_snapshot()
    if mark_last_min is not None and mark_last_min > 0 and _snapshot_buffer:
        now_utc = datetime.utcnow()
        target = now_utc - timedelta(minutes=mark_last_min)
        candidates = [(ts_iso, slim) for (ts_iso, slim) in _snapshot_buffer if ts_iso != result["timestamp"]]
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
