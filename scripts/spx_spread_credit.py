#!/usr/bin/env python3
"""
SPX call and put credit spread scanner: list spreads from current price outward
with mark and range until mark is >= threshold (default $0.20).
Shows both call side and put side.
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
    )
    from public_api_sdk.auth_config import ApiKeyAuthConfig

from get_option_chain import get_option_expirations, parse_osi_symbol


SYMBOL = "SPX"


def _norm_exp(exp):
    if hasattr(exp, "strftime"):
        return exp.strftime("%Y-%m-%d")
    return str(exp)


def _resolve_expiration(client):
    expirations = get_option_expirations(client, SYMBOL, instrument_type=InstrumentType.INDEX)
    if not expirations:
        return None
    today_str = date.today().isoformat()
    for exp in expirations:
        if _norm_exp(exp) == today_str:
            return _norm_exp(exp)
    return _norm_exp(expirations[0])


def _fetch_spx_price(client):
    """Return current SPX last price or None."""
    try:
        instruments = [OrderInstrument(symbol=SYMBOL, type=InstrumentType.INDEX)]
        quotes = client.get_quotes(instruments)
        if quotes and len(quotes) > 0:
            return getattr(quotes[0], "last", None)
    except Exception:
        pass
    return None


def _fetch_chain(client, expiration_date):
    request = OptionChainRequest(
        instrument=OrderInstrument(symbol=SYMBOL, type=InstrumentType.INDEX),
        expiration_date=expiration_date,
    )
    chain = client.get_option_chain(request)
    calls = getattr(chain, "calls", []) or []
    puts = getattr(chain, "puts", []) or []
    return calls, puts


def _build_strike_map(option_list):
    """Return dict strike -> (bid, ask). Strikes as float for sorting."""
    out = {}
    for opt in option_list:
        osi = opt.instrument.symbol if hasattr(opt, "instrument") else None
        if not osi:
            continue
        strike = parse_osi_symbol(osi)
        if strike is None:
            continue
        bid = getattr(opt, "bid", None)
        ask = getattr(opt, "ask", None)
        out[strike] = (bid, ask)
    return out


def _float_or_none(x):
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def run(mark_above=0.20):
    secret = get_api_secret()
    account_id = get_account_id()
    if not secret:
        print("Error: PUBLIC_COM_SECRET is not set.")
        sys.exit(1)
    if not account_id:
        print("Error: PUBLIC_COM_ACCOUNT_ID is not set.")
        sys.exit(1)

    try:
        client = PublicApiClient(
            ApiKeyAuthConfig(api_secret_key=secret),
            config=PublicApiClientConfiguration(default_account_number=account_id),
        )

        spx_now = _fetch_spx_price(client)
        if spx_now is None:
            print("Error: Could not fetch current SPX price.")
            sys.exit(1)
        spx_now = _float_or_none(spx_now)
        if spx_now is None:
            print("Error: Invalid SPX price.")
            sys.exit(1)

        expiration_date = _resolve_expiration(client)
        if not expiration_date:
            print("Error: No SPX option expirations found.")
            sys.exit(1)

        calls, puts = _fetch_chain(client, expiration_date)
        call_map = _build_strike_map(calls)
        put_map = _build_strike_map(puts)

        def print_side(title, strike_map, strikes_sorted, start_idx, pair_order_ascending):
            """Print spreads until mark >= mark_above. pair_order_ascending: (short, long) order in strikes_sorted."""
            print(f"\n{title}")
            print(f"  {'Spread':<14} {'Mark':>10}   {'Range':<20}")
            print(f"  {'-'*14} {'-'*10}   {'-'*20}")
            for i in range(start_idx, len(strikes_sorted) - 1):
                k_first = strikes_sorted[i]
                k_second = strikes_sorted[i + 1]
                if pair_order_ascending:
                    k_short, k_long = k_first, k_second
                else:
                    k_short, k_long = k_second, k_first  # put: short is higher strike
                spread_label = f"{int(k_short)}/{int(k_long)}"
                bid_short, ask_short = strike_map.get(k_short, (None, None))
                bid_long, ask_long = strike_map.get(k_long, (None, None))
                b_s = _float_or_none(bid_short)
                a_s = _float_or_none(ask_short)
                b_l = _float_or_none(bid_long)
                a_l = _float_or_none(ask_long)
                if None in (b_s, a_s, b_l, a_l):
                    mark_str = "--"
                    range_str = "--"
                    mark_val = None
                else:
                    mid_short = (b_s + a_s) / 2
                    mid_long = (b_l + a_l) / 2
                    mark = mid_short - mid_long
                    r_min = b_s - a_l
                    r_max = a_s - b_l
                    mark_str = f"${mark:.2f}"
                    range_str = f"${r_min:.2f} – ${r_max:.2f}"
                    mark_val = mark
                if mark_val is not None and mark_val < mark_above:
                    return  # stop when mark drops below threshold
                print(f"  {spread_label:<14} {mark_str:>10}   {range_str:<20}")

        print("=" * 60)
        print(f"SPX credit spreads — until mark >= ${mark_above:.2f} (expiration: {expiration_date})")
        print(f"SPX now: {spx_now:,.2f}")
        print("=" * 60)

        if call_map:
            strikes_asc = sorted(call_map.keys())
            start_call = 0
            for i, k in enumerate(strikes_asc):
                if k >= spx_now:
                    start_call = i
                    break
            print_side("CALL credit spreads (short/low strike, long/high strike)", call_map, strikes_asc, start_call, True)
        else:
            print("\nCALL credit spreads: No call options in chain.")

        if put_map:
            strikes_desc = sorted(put_map.keys(), reverse=True)
            start_put = 0
            for i, k in enumerate(strikes_desc):
                if k <= spx_now:
                    start_put = i
                    break
            print_side("PUT credit spreads (short/high strike, long/low strike)", put_map, strikes_desc, start_put, True)
        else:
            print("\nPUT credit spreads: No put options in chain.")

        print("\n" + "=" * 60)
        client.close()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="SPX call credit spread scanner (mark and range until mark above threshold)",
    )
    parser.add_argument(
        "--mark-above",
        type=float,
        default=0.20,
        help="Stop when spread mark is above this value (default: 0.20)",
    )
    args = parser.parse_args()
    run(mark_above=args.mark_above)


if __name__ == "__main__":
    main()
