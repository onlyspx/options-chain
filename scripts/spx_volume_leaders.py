#!/usr/bin/env python3
"""
SPX option chain volume leaders: top volume now, or top volume in last 5 minutes.
Uses SPX today's expiration (same-day if available, else nearest).
"""
import argparse
import subprocess
import sys
from datetime import date

from config import get_api_secret, get_account_id

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
    print("Installing required dependency: publicdotcom-py...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "publicdotcom-py==0.1.8"])
    from public_api_sdk import (
        PublicApiClient,
        PublicApiClientConfiguration,
        OrderInstrument,
        InstrumentType,
        OptionChainRequest,
        OptionExpirationsRequest,
    )
    from public_api_sdk.auth_config import ApiKeyAuthConfig

from get_option_chain import get_option_expirations, parse_osi_symbol


# ANSI colors for terminal (call=green, put=red)
COLOR_CALL = "\033[32m"   # green
COLOR_PUT = "\033[31m"    # red
COLOR_RESET = "\033[0m"

SYMBOL = "SPX"


def _norm_exp(exp):
    """Normalize expiration to YYYY-MM-DD string."""
    if hasattr(exp, "strftime"):
        return exp.strftime("%Y-%m-%d")
    return str(exp)


def _resolve_expiration(client):
    """Resolve SPX expiration: same-day if available, else nearest. Returns (exp_date_str, is_today)."""
    expirations = get_option_expirations(client, SYMBOL, instrument_type=InstrumentType.INDEX)
    if not expirations:
        return None, False
    today_str = date.today().isoformat()
    for exp in expirations:
        if _norm_exp(exp) == today_str:
            return _norm_exp(exp), True
    return _norm_exp(expirations[0]), False


def _fetch_chain(client, expiration_date):
    """Fetch SPX option chain for given expiration. Returns (calls, puts)."""
    request = OptionChainRequest(
        instrument=OrderInstrument(symbol=SYMBOL, type=InstrumentType.INDEX),
        expiration_date=expiration_date,
    )
    chain = client.get_option_chain(request)
    calls = getattr(chain, "calls", []) or []
    puts = getattr(chain, "puts", []) or []
    return calls, puts


def _build_rows(calls, puts):
    """Build list of (osi, side, strike, volume, bid, ask) for all options."""
    rows = []

    def add(opt_list, side):
        for opt in opt_list:
            osi = opt.instrument.symbol if hasattr(opt, "instrument") else "N/A"
            strike = parse_osi_symbol(osi)
            volume = getattr(opt, "volume", None)
            bid = getattr(opt, "bid", None)
            ask = getattr(opt, "ask", None)
            rows.append((osi, side, strike, volume, bid, ask))

    add(calls, "CALL")
    add(puts, "PUT")
    return rows


def _print_table(rows, expiration_date, title_suffix, volume_key="volume", top=20):
    """Print top N rows as table. volume_key is 'volume' or 'delta' for column label."""
    # Sort by volume/delta desc (treat None as 0)
    sorted_rows = sorted(
        rows,
        key=lambda r: (r[3] or 0) if isinstance(r[3], (int, float)) else 0,
        reverse=True,
    )
    top_rows = sorted_rows[:top]

    print("=" * 70)
    print(f"SPX volume leaders (expiration: {expiration_date}) — {title_suffix}")
    print("=" * 70)
    print(f"  {'Rank':<6} {'Strike side':<18} {volume_key.capitalize():>10}   {'Bid':>10}   {'Ask':>10}")
    print(f"  {'-'*6} {'-'*18} {'-'*10}   {'-'*10}   {'-'*10}")

    for i, (osi, side, strike, vol, bid, ask) in enumerate(top_rows, 1):
        strike_str = str(int(strike)) if strike is not None else "--"
        side_lower = side.lower()
        color = COLOR_CALL if side == "CALL" else COLOR_PUT
        visible = f"{strike_str} {side_lower}"
        strike_side = f"{strike_str} {color}{side_lower}{COLOR_RESET}"
        vol_str = f"{int(vol):,}" if vol is not None else "0"
        bid_str = f"${float(bid):,.2f}" if bid is not None else "--"
        ask_str = f"${float(ask):,.2f}" if ask is not None else "--"
        pad = max(0, 18 - len(visible))
        print(f"  {i:<6} {strike_side}{' ' * pad} {vol_str:>10}   {bid_str:>10}   {ask_str:>10}")

    print("=" * 70)


def run_top_now(client, expiration_date, is_today, top):
    """Fetch once, rank by volume, print top N."""
    calls, puts = _fetch_chain(client, expiration_date)
    rows = _build_rows(calls, puts)
    if not rows:
        print("No option data in chain.")
        return
    if not is_today:
        print(f"Note: Using nearest expiration (not same-day): {expiration_date}\n")
    _print_table(rows, expiration_date, "top volume now", volume_key="volume", top=top)


def run_last_5_min(client, expiration_date, is_today, top):
    """Fetch, store snapshot, wait 5 min, fetch again, rank by delta, print top N."""
    calls, puts = _fetch_chain(client, expiration_date)
    rows = _build_rows(calls, puts)
    if not rows:
        print("No option data in chain.")
        return
    snapshot = {r[0]: (r[3] or 0) for r in rows}

    print("Waiting 5 minutes...")
    import time
    time.sleep(300)

    calls2, puts2 = _fetch_chain(client, expiration_date)
    rows2 = _build_rows(calls2, puts2)
    # Build (osi, side, strike, delta, bid, ask) using current volume - snapshot
    delta_rows = []
    for osi, side, strike, vol_now, bid, ask in rows2:
        vol_then = snapshot.get(osi, 0)
        vn = vol_now if vol_now is not None else 0
        if not isinstance(vn, (int, float)):
            vn = 0
        delta = max(0, vn - vol_then)
        delta_rows.append((osi, side, strike, delta, bid, ask))

    if not is_today:
        print(f"Note: Using nearest expiration (not same-day): {expiration_date}\n")
    _print_table(delta_rows, expiration_date, "top volume in last 5 minutes", volume_key="delta", top=top)


def main():
    parser = argparse.ArgumentParser(
        description="SPX option chain volume leaders (top volume now or in last 5 minutes)",
        epilog="Examples:\n  %(prog)s --top 20\n  %(prog)s --last-5-min --top 30",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--top", type=int, default=20, help="Number of leaders to show (default: 20)")
    parser.add_argument("--expiration", help="Override expiration (YYYY-MM-DD). Default: today or nearest.")
    parser.add_argument(
        "--last-5-min",
        action="store_true",
        help="Wait 5 minutes and show top by volume delta (two fetches).",
    )
    args = parser.parse_args()

    secret = get_api_secret()
    account_id = get_account_id()
    if not secret:
        print("Error: PUBLIC_COM_SECRET is not set.")
        sys.exit(1)
    if not account_id:
        print("Error: No account ID provided. Set PUBLIC_COM_ACCOUNT_ID or use --account-id.")
        sys.exit(1)

    try:
        client = PublicApiClient(
            ApiKeyAuthConfig(api_secret_key=secret),
            config=PublicApiClientConfiguration(default_account_number=account_id),
        )

        if args.expiration:
            expiration_date = args.expiration
            is_today = expiration_date == date.today().isoformat()
        else:
            expiration_date, is_today = _resolve_expiration(client)
            if not expiration_date:
                print("Error: No SPX option expirations found.")
                sys.exit(1)

        if args.last_5_min:
            run_last_5_min(client, expiration_date, is_today, args.top)
        else:
            run_top_now(client, expiration_date, is_today, args.top)

        client.close()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
