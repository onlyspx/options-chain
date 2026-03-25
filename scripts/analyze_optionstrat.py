#!/usr/bin/env python3
"""
analyze_optionstrat.py

Parse an OptionsStrat URL, fetch live quotes + greeks from Public.com,
compute net greeks, net debit/credit, and breakeven points at short expiry.

Usage:
    python3 analyze_optionstrat.py "https://optionstrat.com/build/calendar-call-spread/SPX/-.SPXW260408C6590,.SPXW260422C6590"
"""

import argparse
import math
import re
import sys
from datetime import date, datetime
from urllib.parse import urlparse

from config import get_api_secret, get_account_id

try:
    from public_api_sdk import (
        PublicApiClient,
        PublicApiClientConfiguration,
        OrderInstrument,
        InstrumentType,
    )
    from public_api_sdk.auth_config import ApiKeyAuthConfig
except ImportError:
    import subprocess
    print("Installing required dependency: publicdotcom-py...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "publicdotcom-py==0.1.8"])
    from public_api_sdk import (
        PublicApiClient,
        PublicApiClientConfiguration,
        OrderInstrument,
        InstrumentType,
    )
    from public_api_sdk.auth_config import ApiKeyAuthConfig


# ── Black-Scholes ────────────────────────────────────────────────────────────

def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S, K, T, r, sigma, opt_type):
    """Black-Scholes option price. opt_type: 'C' or 'P'. T in years."""
    if T <= 1e-9:
        return max(0.0, (S - K) if opt_type == "C" else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if opt_type == "C":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


# ── OptionsStrat URL parser ──────────────────────────────────────────────────

def parse_optionstrat_url(url):
    """
    Parse OptionsStrat URL into leg dicts.

    URL: https://optionstrat.com/build/{strategy}/{underlying}/{legs}
    Legs: comma-separated, '-' prefix = short, optional '2x' quantity prefix
    Symbol: [.]ROOT YYMMDD C/P STRIKE  e.g. .SPXW260408C6590

    Returns: (underlying, strategy, legs)
    Each leg: {osi_symbol, quantity, opt_type, strike, expiry_date, root}
    """
    path = urlparse(url).path
    parts = path.strip("/").split("/")
    # ['build', 'calendar-call-spread', 'SPX', '-.SPXW260408C6590,...']
    if len(parts) < 4 or parts[0] != "build":
        raise ValueError(
            f"Expected a full OptionsStrat build URL, got: {url}\n"
            f"  Use the full URL from your browser, e.g.:\n"
            f"  https://optionstrat.com/build/calendar-call-spread/QQQ/-.QQQ260417C600,.QQQ260430C600\n"
            f"  (Short share links like optionstrat.com/NqO50JkSEaJN are not supported)"
        )

    strategy = parts[1]
    underlying = parts[2]
    legs_raw = parts[3]

    legs = []
    for raw in legs_raw.split(","):
        raw = raw.strip()
        qty = 1

        # Handle optional quantity prefix: -2x or 2x
        m = re.match(r"^(-?)(\d+)x(.+)$", raw)
        if m:
            qty = int(m.group(2)) * (-1 if m.group(1) == "-" else 1)
            raw = m.group(3)
        elif raw.startswith("-"):
            qty = -1
            raw = raw[1:]

        symbol = raw.lstrip(".")  # strip leading dot

        # Parse: ROOT + YYMMDD + C/P + STRIKE
        m = re.match(r"^([A-Z]+)(\d{6})([CP])(\d+(?:\.\d+)?)$", symbol)
        if not m:
            raise ValueError(f"Cannot parse option symbol: '{symbol}'")

        root, expiry_str, opt_type, strike_str = m.groups()
        strike = float(strike_str)
        expiry_date = datetime.strptime(expiry_str, "%y%m%d").date()

        # OSI format: ROOT + YYMMDD + C/P + strike*1000 zero-padded to 8 digits
        strike_osi = str(int(strike * 1000)).zfill(8)
        osi_symbol = f"{root}{expiry_str}{opt_type}{strike_osi}"

        legs.append({
            "osi_symbol": osi_symbol,
            "quantity": qty,
            "opt_type": opt_type,
            "strike": strike,
            "expiry_date": expiry_date,
            "root": root,
        })

    return underlying, strategy, legs


# ── Public.com data fetch ────────────────────────────────────────────────────

def fetch_live_data(client, osi_symbols):
    """
    Fetch quotes + greeks for osi_symbols.
    Returns dict: {osi_symbol: {bid, ask, last, delta, gamma, theta, vega, rho, iv}}
    """
    instruments = [
        OrderInstrument(symbol=s, type=InstrumentType.OPTION) for s in osi_symbols
    ]
    quotes_resp = client.get_quotes(instruments)
    greeks_resp = client.get_option_greeks(osi_symbols=osi_symbols)

    data = {}

    for q in quotes_resp:
        sym = q.instrument.symbol
        data[sym] = {
            "bid":  float(q.bid)  if q.bid  is not None else None,
            "ask":  float(q.ask)  if q.ask  is not None else None,
            "last": float(q.last) if q.last is not None else None,
        }

    for g in greeks_resp.greeks:
        sym = getattr(g, "osi_symbol", None) or getattr(g, "symbol", None)
        gk = g.greeks
        entry = data.setdefault(sym, {})
        entry.update({
            "delta": float(gk.delta)              if gk.delta              is not None else None,
            "gamma": float(gk.gamma)              if gk.gamma              is not None else None,
            "theta": float(gk.theta)              if gk.theta              is not None else None,
            "vega":  float(gk.vega)               if gk.vega               is not None else None,
            "rho":   float(gk.rho)                if gk.rho                is not None else None,
            "iv":    float(gk.implied_volatility) if gk.implied_volatility is not None else None,
        })

    return data


# ── Breakeven calculation ────────────────────────────────────────────────────

def compute_pnl_curve(legs, live_data, net_debit, r=0.045):
    """
    Build a P&L curve at the first (short) expiry across a price range.
    Returns (prices, pnls) lists — per-share values.
    """
    short_expiry = min(l["expiry_date"] for l in legs)

    def pnl_at_price(S):
        total = 0.0
        for leg in legs:
            d = live_data.get(leg["osi_symbol"], {})
            iv = d.get("iv") or 0.20
            T_remaining = max(0.0, (leg["expiry_date"] - short_expiry).days / 365.0)
            val = bs_price(S, leg["strike"], T_remaining, r, iv, leg["opt_type"])
            total += leg["quantity"] * val
        return total - net_debit

    center = sum(l["strike"] for l in legs) / len(legs)
    lo, hi = center * 0.70, center * 1.30
    n = 3000
    prices = [lo + i * (hi - lo) / n for i in range(n + 1)]
    pnls   = [pnl_at_price(p) for p in prices]
    return prices, pnls


def compute_breakevens(prices, pnls):
    """Find zero-crossings in a P&L curve. Returns list of breakeven prices."""
    breakevens = []
    for i in range(len(pnls) - 1):
        if pnls[i] * pnls[i + 1] <= 0 and pnls[i] != pnls[i + 1]:
            be = prices[i] - pnls[i] * (prices[i + 1] - prices[i]) / (pnls[i + 1] - pnls[i])
            breakevens.append(round(be, 2))
    return breakevens


def compute_chance_of_profit(S0, breakevens, T_days, iv, r=0.045):
    """
    Probability underlying stays between breakevens at T using log-normal distribution.
    Uses the long-leg IV as a proxy for the market's expectation.
    """
    if len(breakevens) < 2 or iv is None:
        return None
    T = T_days / 365.0
    lo_be, hi_be = breakevens[0], breakevens[1]

    def prob_above(K):
        if T <= 0:
            return 1.0 if S0 > K else 0.0
        d2 = (math.log(S0 / K) + (r - 0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
        return _norm_cdf(d2)

    return prob_above(lo_be) - prob_above(hi_be)


# ── Main ─────────────────────────────────────────────────────────────────────

def analyze(url):
    underlying, strategy, legs = parse_optionstrat_url(url)
    osi_symbols = [l["osi_symbol"] for l in legs]
    today = date.today()

    secret = get_api_secret()
    account_id = get_account_id()
    if not secret or not account_id:
        print("Error: PUBLIC_COM_SECRET / PUBLIC_COM_ACCOUNT_ID not set.")
        sys.exit(1)

    client = PublicApiClient(
        ApiKeyAuthConfig(api_secret_key=secret),
        config=PublicApiClientConfiguration(default_account_number=account_id),
    )
    try:
        live_data = fetch_live_data(client, osi_symbols)
    finally:
        client.close()

    # ── Header ──────────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print(f"  OPTIONSTRAT ANALYSIS  —  {strategy.replace('-', ' ').title()}")
    print(f"  Underlying : {underlying}     As of: {today}")
    print("=" * 72)

    # ── Per-leg table ────────────────────────────────────────────────────────
    print()
    print(f"  {'SYMBOL':<26} {'DIR':>4} {'EXP':>12} {'BID':>8} {'ASK':>8} {'MID':>8} {'IV':>7}")
    print(f"  {'-'*26} {'-'*4} {'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*7}")

    net_debit = 0.0  # positive = we paid (debit trade)
    net_greeks = {k: 0.0 for k in ("delta", "gamma", "theta", "vega", "rho")}

    MULTIPLIER = 100  # standard options contract multiplier

    for leg in legs:
        sym = leg["osi_symbol"]
        qty = leg["quantity"]
        d = live_data.get(sym, {})

        bid  = d.get("bid")
        ask  = d.get("ask")
        mid  = (bid + ask) / 2.0 if bid is not None and ask is not None else d.get("last")
        iv   = d.get("iv")

        direction = f"+{qty}" if qty > 0 else str(qty)
        exp_str   = leg["expiry_date"].strftime("%Y-%m-%d")
        bid_s  = f"${bid:.2f}"  if bid  is not None else "--"
        ask_s  = f"${ask:.2f}"  if ask  is not None else "--"
        mid_s  = f"${mid:.2f}"  if mid  is not None else "--"
        iv_s   = f"{iv:.1%}"    if iv   is not None else "--"

        print(f"  {sym:<26} {direction:>4} {exp_str:>12} {bid_s:>8} {ask_s:>8} {mid_s:>8} {iv_s:>7}")

        if mid is not None:
            net_debit += qty * mid * MULTIPLIER

        for g in ("delta", "gamma", "theta", "vega", "rho"):
            net_greeks[g] += qty * (d.get(g) or 0.0) * MULTIPLIER

    # ── P&L curve + derived metrics ──────────────────────────────────────────
    short_expiry    = min(l["expiry_date"] for l in legs)
    days_to_short   = (short_expiry - today).days
    prices, pnls_ps = compute_pnl_curve(legs, live_data, net_debit / MULTIPLIER)
    pnls_dollar     = [p * MULTIPLIER for p in pnls_ps]
    breakevens      = compute_breakevens(prices, pnls_ps)
    max_profit      = max(pnls_dollar)
    max_loss        = net_debit  # calendar max loss = debit paid

    long_leg = next((l for l in legs if l["quantity"] > 0), legs[-1])
    long_iv  = (live_data.get(long_leg["osi_symbol"]) or {}).get("iv")
    center_strike = sum(l["strike"] for l in legs) / len(legs)
    cop = compute_chance_of_profit(center_strike, breakevens, days_to_short, long_iv)

    # ── Summary table (mirrors OptionsStrat layout) ───────────────────────────
    print()
    print(f"  {'Field':<24} {'Our Script':>14}   {'OptionsStrat Free':>17}")
    print(f"  {'-'*24} {'-'*14}   {'-'*17}")

    print(f"  {'Net Debit':<24} {'${:.2f}'.format(net_debit):>14}   {'visible':>17}")
    print(f"  {'Max Loss':<24} {'${:.2f}'.format(max_loss):>14}   {'visible':>17}")
    print(f"  {'Max Profit':<24} {'${:.2f}'.format(max_profit):>14}   {'visible':>17}")

    if breakevens and len(breakevens) == 2:
        be_str = f"${breakevens[0]:,.2f} – ${breakevens[1]:,.2f}"
    elif breakevens:
        be_str = f"${breakevens[0]:,.2f}"
    else:
        be_str = "n/a"
    print(f"  {'Breakevens':<24} {be_str:>14}   {'visible':>17}")

    if breakevens and len(breakevens) == 2:
        width_str = f"${breakevens[1]-breakevens[0]:.0f} wide"
        print(f"  {'  └ profit zone':<24} {width_str:>14}   {'visible (graph)':>17}")

    cop_str = f"{cop:.1%}" if cop is not None else "n/a"
    print(f"  {'Chance of Profit':<24} {cop_str:>14}   {'LOCKED':>17}")
    print(f"  {'-'*24} {'-'*14}   {'-'*17}")
    print(f"  {'Delta':<24} {net_greeks['delta']:>14.2f}   {'LOCKED':>17}")
    print(f"  {'Theta ($/day)':<24} {net_greeks['theta']:>14.2f}   {'LOCKED':>17}")
    print(f"  {'Gamma':<24} {net_greeks['gamma']:>14.4f}   {'LOCKED':>17}")
    print(f"  {'Vega':<24} {net_greeks['vega']:>14.2f}   {'LOCKED':>17}")
    print(f"  {'Rho':<24} {net_greeks['rho']:>14.2f}   {'LOCKED':>17}")

    print()
    print("=" * 72)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Analyze an OptionsStrat trade using Public.com live data",
        epilog=(
            "Example:\n"
            "  python3 analyze_optionstrat.py 'https://optionstrat.com/build/"
            "calendar-call-spread/SPX/-.SPXW260408C6590,.SPXW260422C6590'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="OptionsStrat URL")
    args = parser.parse_args()
    try:
        analyze(args.url)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
