#!/usr/bin/env python3
"""
suggest_trades.py

Analyze an options chain for a given ticker + outlook and suggest
the top trades with live greeks, risk metrics, and OptionsStrat links.

Usage:
    python3 suggest_trades.py NVDA --outlook bullish
    python3 suggest_trades.py QQQ  --outlook neutral
    python3 suggest_trades.py SPY  --outlook bearish
"""

import argparse
import math
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from config import get_api_secret, get_account_id

try:
    from public_api_sdk import (
        PublicApiClient, PublicApiClientConfiguration,
        OrderInstrument, InstrumentType,
        OptionChainRequest, OptionExpirationsRequest,
    )
    from public_api_sdk.auth_config import ApiKeyAuthConfig
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "publicdotcom-py==0.1.8"])
    from public_api_sdk import (
        PublicApiClient, PublicApiClientConfiguration,
        OrderInstrument, InstrumentType,
        OptionChainRequest, OptionExpirationsRequest,
    )
    from public_api_sdk.auth_config import ApiKeyAuthConfig

INDEX_SYMBOLS = frozenset({"SPX", "NDX", "VIX", "RUT", "CBTX"})
MULTIPLIER = 100
RISK_FREE_RATE = 0.045


# ── Black-Scholes ────────────────────────────────────────────────────────────

def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def prob_above(S, K, T, iv, r=RISK_FREE_RATE):
    """Risk-neutral probability that S > K at expiry T (years)."""
    if T <= 1e-9 or iv <= 0:
        return 1.0 if S > K else 0.0
    d2 = (math.log(S / K) + (r - 0.5 * iv**2) * T) / (iv * math.sqrt(T))
    return _norm_cdf(d2)

def bs_price(S, K, T, r, sigma, opt_type):
    if T <= 1e-9:
        return max(0.0, (S - K) if opt_type == "C" else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if opt_type == "C":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class OptionData:
    osi: str
    symbol: str       # underlying
    expiry: date
    strike: float
    opt_type: str     # 'C' or 'P'
    bid: float
    ask: float
    volume: int
    oi: int
    # filled after greeks fetch
    delta: Optional[float] = None
    theta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    iv: Optional[float] = None

    @property
    def mid(self):
        return (self.bid + self.ask) / 2.0

    @property
    def spread_pct(self):
        return (self.ask - self.bid) / self.mid if self.mid > 0 else 999


@dataclass
class Trade:
    name: str
    description: str
    strategy_slug: str        # for OptionsStrat URL
    underlying: str
    legs: list                 # list of (qty, OptionData)
    net_debit: float           # positive = paid, negative = received
    max_profit: float
    max_loss: float
    breakevens: list[float]
    pop: float                 # 0–1
    net_delta: float
    net_theta: float
    net_vega: float
    score: float = 0.0

    @property
    def optionstrat_url(self):
        parts = []
        for qty, opt in self.legs:
            strike = opt.strike
            strike_str = str(int(strike)) if strike == int(strike) else str(strike)
            exp = opt.expiry.strftime("%y%m%d")
            sym = f".{opt.symbol}{exp}{opt.opt_type}{strike_str}"
            parts.append(f"-{sym}" if qty < 0 else sym)
        legs_str = ",".join(parts)
        return f"https://optionstrat.com/build/{self.strategy_slug}/{self.underlying}/{legs_str}"


# ── Client helpers ───────────────────────────────────────────────────────────

def make_client():
    secret = get_api_secret()
    account_id = get_account_id()
    if not secret or not account_id:
        print("Error: PUBLIC_COM_SECRET / PUBLIC_COM_ACCOUNT_ID not set.")
        sys.exit(1)
    return PublicApiClient(
        ApiKeyAuthConfig(api_secret_key=secret),
        config=PublicApiClientConfiguration(default_account_number=account_id),
    )

def inst_type(symbol):
    return InstrumentType.INDEX if symbol.upper() in INDEX_SYMBOLS else InstrumentType.EQUITY

def get_spot(client, symbol):
    instruments = [OrderInstrument(symbol=symbol.upper(), type=inst_type(symbol))]
    quotes = client.get_quotes(instruments)
    return float(quotes[0].last)

def get_expirations(client, symbol):
    req = OptionExpirationsRequest(
        instrument=OrderInstrument(symbol=symbol.upper(), type=inst_type(symbol))
    )
    resp = client.get_option_expirations(req)
    return resp.expirations if hasattr(resp, "expirations") else []

def get_chain(client, symbol, expiry_date) -> list[OptionData]:
    """Fetch full chain for a symbol+expiry, return list of OptionData."""
    exp_str = expiry_date.strftime("%Y-%m-%d") if hasattr(expiry_date, "strftime") else str(expiry_date)
    req = OptionChainRequest(
        instrument=OrderInstrument(symbol=symbol.upper(), type=inst_type(symbol)),
        expiration_date=exp_str
    )
    chain_resp = client.get_option_chain(req)
    results = []
    for opt_type_list, opt_char in [(chain_resp.calls, "C"), (chain_resp.puts, "P")]:
        for opt in (opt_type_list or []):
            osi = opt.instrument.symbol
            strike_raw = int(osi[-8:]) / 1000.0
            bid  = float(opt.bid)  if opt.bid  is not None else 0.0
            ask  = float(opt.ask)  if opt.ask  is not None else 0.0
            vol  = int(opt.volume) if opt.volume is not None else 0
            oi   = int(opt.open_interest) if opt.open_interest is not None else 0
            results.append(OptionData(
                osi=osi, symbol=symbol.upper(), expiry=expiry_date,
                strike=strike_raw, opt_type=opt_char,
                bid=bid, ask=ask, volume=vol, oi=oi,
            ))
    return results

def enrich_with_greeks(client, options: list[OptionData]):
    """Fetch greeks for a list of OptionData objects and fill in place."""
    osi_list = [o.osi for o in options]
    resp = client.get_option_greeks(osi_symbols=osi_list)
    greeks_map = {}
    for g in resp.greeks:
        sym = getattr(g, "osi_symbol", None) or getattr(g, "symbol", None)
        gk = g.greeks
        greeks_map[sym] = {
            "delta": float(gk.delta)              if gk.delta              is not None else None,
            "theta": float(gk.theta)              if gk.theta              is not None else None,
            "gamma": float(gk.gamma)              if gk.gamma              is not None else None,
            "vega":  float(gk.vega)               if gk.vega               is not None else None,
            "iv":    float(gk.implied_volatility) if gk.implied_volatility is not None else None,
        }
    for opt in options:
        g = greeks_map.get(opt.osi, {})
        opt.delta = g.get("delta")
        opt.theta = g.get("theta")
        opt.gamma = g.get("gamma")
        opt.vega  = g.get("vega")
        opt.iv    = g.get("iv")


# ── Chain filtering ──────────────────────────────────────────────────────────

def filter_chain(chain: list[OptionData], spot: float,
                 opt_type: str, lo_pct: float, hi_pct: float) -> list[OptionData]:
    """Keep options of given type within [spot*lo_pct, spot*hi_pct] with decent liquidity."""
    lo, hi = spot * lo_pct, spot * hi_pct
    return [
        o for o in chain
        if o.opt_type == opt_type
        and lo <= o.strike <= hi
        and o.mid > 0.05
        and o.spread_pct < 0.30   # max 30% bid/ask spread
    ]

def nearest_strike(options: list[OptionData], target: float) -> Optional[OptionData]:
    if not options:
        return None
    return min(options, key=lambda o: abs(o.strike - target))

def find_strike(options: list[OptionData], target: float, tol: float = 3.0) -> Optional[OptionData]:
    candidates = [o for o in options if abs(o.strike - target) <= tol]
    return min(candidates, key=lambda o: abs(o.strike - target)) if candidates else None


# ── Trade builders ────────────────────────────────────────────────────────────

def make_bull_call_spreads(calls: list[OptionData], spot: float, expiry: date, symbol: str) -> list[Trade]:
    """Bull call debit spreads: buy lower call, sell higher call."""
    today = date.today()
    T = (expiry - today).days / 365.0
    trades = []

    # candidate long strikes: ATM to ATM+5%
    # candidate short strikes: long+5 to long+15%
    atm = nearest_strike(calls, spot)
    if not atm:
        return []

    long_targets  = [atm.strike, atm.strike + 2.5, atm.strike + 5]
    short_offsets = [7.5, 10, 12.5, 15, 20]

    for lt in long_targets:
        long_opt = find_strike(calls, lt)
        if not long_opt or long_opt.delta is None:
            continue
        for offset in short_offsets:
            short_opt = find_strike(calls, lt + offset)
            if not short_opt or short_opt.delta is None:
                continue

            debit    = (long_opt.mid - short_opt.mid) * MULTIPLIER
            width    = (short_opt.strike - long_opt.strike) * MULTIPLIER
            max_loss = debit
            max_prof = width - debit
            if max_prof <= 0 or max_loss <= 0:
                continue

            be = long_opt.strike + (debit / MULTIPLIER)
            iv = long_opt.iv or 0.50
            pop = prob_above(spot, be, T, iv) if T > 0 else (1.0 if spot > be else 0.0)

            net_delta = (long_opt.delta - short_opt.delta) * MULTIPLIER
            net_theta = (long_opt.theta - short_opt.theta) * MULTIPLIER
            net_vega  = (long_opt.vega  - short_opt.vega)  * MULTIPLIER

            score = (pop * 0.4) + ((max_prof / (max_prof + max_loss)) * 0.6)

            trades.append(Trade(
                name=f"Bull Call Spread  {int(long_opt.strike)}/{int(short_opt.strike)}C  {expiry.strftime('%b%d')}",
                description=f"Buy {int(long_opt.strike)}C / Sell {int(short_opt.strike)}C",
                strategy_slug="bull-call-spread",
                underlying=symbol,
                legs=[(-1, short_opt), (+1, long_opt)],
                net_debit=debit,
                max_profit=max_prof,
                max_loss=max_loss,
                breakevens=[round(be, 2)],
                pop=pop,
                net_delta=net_delta,
                net_theta=net_theta,
                net_vega=net_vega,
                score=score,
            ))

    trades.sort(key=lambda t: t.score, reverse=True)
    return trades[:3]


def make_bull_put_spreads(puts: list[OptionData], spot: float, expiry: date, symbol: str) -> list[Trade]:
    """Bull put credit spreads: sell higher put, buy lower put."""
    today = date.today()
    T = (expiry - today).days / 365.0
    trades = []

    atm = nearest_strike(puts, spot)
    if not atm:
        return []

    # short put: 3–12% OTM; long put: 5–10 below short
    short_targets = [spot * p for p in (0.97, 0.94, 0.91, 0.88)]
    widths        = [5, 7.5, 10, 12.5]

    for st in short_targets:
        short_opt = nearest_strike([p for p in puts if p.strike <= st + 1], st)
        if not short_opt or short_opt.delta is None:
            continue
        for w in widths:
            long_opt = find_strike(puts, short_opt.strike - w)
            if not long_opt or long_opt.delta is None:
                continue

            credit   = (short_opt.mid - long_opt.mid) * MULTIPLIER
            width    = (short_opt.strike - long_opt.strike) * MULTIPLIER
            max_prof = credit
            max_loss = width - credit
            if max_prof <= 0 or max_loss <= 0 or credit < 20:
                continue

            be = short_opt.strike - (credit / MULTIPLIER)
            iv = short_opt.iv or 0.50
            pop = prob_above(spot, be, T, iv)

            net_delta = (-short_opt.delta + long_opt.delta) * MULTIPLIER
            net_theta = (-short_opt.theta + long_opt.theta) * MULTIPLIER
            net_vega  = (-short_opt.vega  + long_opt.vega)  * MULTIPLIER

            rr = max_prof / max_loss if max_loss > 0 else 0
            score = (pop * 0.5) + (min(rr, 1.0) * 0.5)

            trades.append(Trade(
                name=f"Bull Put Spread   {int(short_opt.strike)}/{int(long_opt.strike)}P  {expiry.strftime('%b%d')}",
                description=f"Sell {int(short_opt.strike)}P / Buy {int(long_opt.strike)}P",
                strategy_slug="bull-put-spread",
                underlying=symbol,
                legs=[(-1, short_opt), (+1, long_opt)],
                net_debit=-credit,
                max_profit=max_prof,
                max_loss=max_loss,
                breakevens=[round(be, 2)],
                pop=pop,
                net_delta=net_delta,
                net_theta=net_theta,
                net_vega=net_vega,
                score=score,
            ))

    trades.sort(key=lambda t: t.score, reverse=True)
    return trades[:3]


def make_call_calendars(near_calls: list[OptionData], far_calls: list[OptionData],
                        spot: float, near_expiry: date, symbol: str) -> list[Trade]:
    """Calendar call spreads: sell near-term, buy far-term at same strike."""
    today = date.today()
    T_near = (near_expiry - today).days / 365.0
    trades = []

    # Slightly OTM strikes are best for calendars on bullish bias
    strike_targets = [spot * p for p in (1.00, 1.02, 1.03, 1.05)]

    for st in strike_targets:
        near_opt = nearest_strike([c for c in near_calls if abs(c.strike - st) < 5], st)
        far_opt  = nearest_strike([c for c in far_calls  if abs(c.strike - st) < 5], st)
        if not near_opt or not far_opt:
            continue
        if near_opt.delta is None or far_opt.delta is None:
            continue

        debit    = (far_opt.mid - near_opt.mid) * MULTIPLIER
        if debit <= 0:
            continue

        # estimate max profit ≈ far_mid at near_expiry if spot pins strike
        # using BS: value of far leg at near expiry when S = strike
        iv_far = far_opt.iv or 0.50
        T_far_remaining = (far_opt.expiry - near_expiry).days / 365.0
        max_prof_ps = bs_price(near_opt.strike, near_opt.strike, T_far_remaining,
                               RISK_FREE_RATE, iv_far, "C")
        max_prof = max_prof_ps * MULTIPLIER - debit

        # rough breakevens at near expiry via scan
        breakevens = _calendar_breakevens(near_opt, far_opt, debit / MULTIPLIER, spot)

        pop = 0.0
        if len(breakevens) == 2:
            pop = prob_above(spot, breakevens[0], T_near, near_opt.iv or 0.50) - \
                  prob_above(spot, breakevens[1], T_near, near_opt.iv or 0.50)

        net_delta = (-near_opt.delta + far_opt.delta) * MULTIPLIER
        net_theta = (-near_opt.theta + far_opt.theta) * MULTIPLIER
        net_vega  = (-near_opt.vega  + far_opt.vega)  * MULTIPLIER

        score = (pop * 0.5) + (max(net_theta, 0) / 20.0 * 0.3) + (0.2 if net_vega > 0 else 0)

        trades.append(Trade(
            name=f"Call Calendar      {int(near_opt.strike)}C  {near_expiry.strftime('%b%d')}/{far_opt.expiry.strftime('%b%d')}",
            description=f"Sell {near_expiry.strftime('%b%d')} {int(near_opt.strike)}C / Buy {far_opt.expiry.strftime('%b%d')} {int(far_opt.strike)}C",
            strategy_slug="calendar-call-spread",
            underlying=symbol,
            legs=[(-1, near_opt), (+1, far_opt)],
            net_debit=debit,
            max_profit=max(max_prof, 0),
            max_loss=debit,
            breakevens=breakevens,
            pop=pop,
            net_delta=net_delta,
            net_theta=net_theta,
            net_vega=net_vega,
            score=score,
        ))

    trades.sort(key=lambda t: t.score, reverse=True)
    return trades[:2]


def _calendar_breakevens(near_opt, far_opt, net_debit_ps, spot):
    """Estimate calendar breakevens at near expiry via price scan."""
    center = near_opt.strike
    lo, hi = center * 0.80, center * 1.20
    n = 2000
    iv_near = near_opt.iv or 0.50
    iv_far  = far_opt.iv  or 0.50
    T_far_rem = (far_opt.expiry - near_opt.expiry).days / 365.0

    def pnl(S):
        short_val = bs_price(S, near_opt.strike, 0, RISK_FREE_RATE, iv_near, "C")
        long_val  = bs_price(S, far_opt.strike, T_far_rem, RISK_FREE_RATE, iv_far, "C")
        return (long_val - short_val) - net_debit_ps

    prices = [lo + i * (hi - lo) / n for i in range(n + 1)]
    pnls   = [pnl(p) for p in prices]

    breakevens = []
    for i in range(len(pnls) - 1):
        if pnls[i] * pnls[i + 1] <= 0 and pnls[i] != pnls[i + 1]:
            be = prices[i] - pnls[i] * (prices[i + 1] - prices[i]) / (pnls[i + 1] - pnls[i])
            breakevens.append(round(be, 2))
    return breakevens


def make_iron_condors(calls: list[OptionData], puts: list[OptionData],
                      spot: float, expiry: date, symbol: str) -> list[Trade]:
    """Iron condor: sell OTM call spread + sell OTM put spread."""
    today = date.today()
    T = (expiry - today).days / 365.0
    trades = []

    combos = [
        (0.93, 0.88, 1.07, 1.12),  # moderate: 7% OTM wings
        (0.90, 0.85, 1.10, 1.15),  # wide: 10% OTM wings
    ]

    for sp_pct, lp_pct, sc_pct, lc_pct in combos:
        sp = nearest_strike([p for p in puts  if p.strike <= spot * sp_pct + 2], spot * sp_pct)
        lp = nearest_strike([p for p in puts  if p.strike <= spot * lp_pct + 2], spot * lp_pct)
        sc = nearest_strike([c for c in calls if c.strike >= spot * sc_pct - 2], spot * sc_pct)
        lc = nearest_strike([c for c in calls if c.strike >= spot * lc_pct - 2], spot * lc_pct)

        if not all([sp, lp, sc, lc]):
            continue
        if any(o.delta is None for o in [sp, lp, sc, lc]):
            continue
        if sp.strike == lp.strike or sc.strike == lc.strike:
            continue

        credit   = (sp.mid - lp.mid + sc.mid - lc.mid) * MULTIPLIER
        put_width = (sp.strike - lp.strike) * MULTIPLIER
        call_width= (lc.strike - sc.strike) * MULTIPLIER
        max_loss  = max(put_width, call_width) - credit
        max_prof  = credit

        be_lo = sp.strike - credit / MULTIPLIER
        be_hi = sc.strike + credit / MULTIPLIER

        iv = sp.iv or sc.iv or 0.50
        pop = prob_above(spot, be_lo, T, iv) - prob_above(spot, be_hi, T, iv)

        net_delta = (-sp.delta + lp.delta - sc.delta + lc.delta) * MULTIPLIER
        net_theta = (-sp.theta + lp.theta - sc.theta + lc.theta) * MULTIPLIER
        net_vega  = (-sp.vega  + lp.vega  - sc.vega  + lc.vega)  * MULTIPLIER

        rr = max_prof / max_loss if max_loss > 0 else 0
        score = pop * 0.6 + min(rr, 1.0) * 0.4

        trades.append(Trade(
            name=f"Iron Condor        {int(lp.strike)}/{int(sp.strike)}P + {int(sc.strike)}/{int(lc.strike)}C  {expiry.strftime('%b%d')}",
            description=f"Sell {int(sp.strike)}P/{int(sc.strike)}C | Buy {int(lp.strike)}P/{int(lc.strike)}C",
            strategy_slug="iron-condor",
            underlying=symbol,
            legs=[(-1, sp), (+1, lp), (-1, sc), (+1, lc)],
            net_debit=-credit,
            max_profit=max_prof,
            max_loss=max_loss,
            breakevens=[round(be_lo, 2), round(be_hi, 2)],
            pop=pop,
            net_delta=net_delta,
            net_theta=net_theta,
            net_vega=net_vega,
            score=score,
        ))

    trades.sort(key=lambda t: t.score, reverse=True)
    return trades[:2]


# ── Display ───────────────────────────────────────────────────────────────────

def display_trade(rank: int, trade: Trade):
    label = "CREDIT" if trade.net_debit < 0 else "DEBIT"
    amount = abs(trade.net_debit)
    be_str = " / ".join(f"${b:,.2f}" for b in trade.breakevens) if trade.breakevens else "n/a"

    print(f"\n  #{rank}  {trade.name}")
    print(f"       {trade.description}")
    print(f"  {'─'*65}")
    print(f"  {'Net '+label+':':<16} ${amount:>7.2f}   {'Max Profit:':<14} ${trade.max_profit:>7.2f}")
    print(f"  {'Max Loss:':<16} ${trade.max_loss:>7.2f}   {'Breakeven(s):':<14} {be_str}")
    print(f"  {'PoP:':<16} {trade.pop:>7.1%}   {'R/R:':<14} 1:{trade.max_profit/trade.max_loss:.1f}" if trade.max_loss > 0 else "")
    print(f"  {'Net Delta:':<16} {trade.net_delta:>7.2f}   {'Net Theta/day:':<14} ${trade.net_theta:>6.2f}   Vega: {trade.net_vega:>6.2f}")
    print(f"  OptionsStrat: {trade.optionstrat_url}")


# ── Main ─────────────────────────────────────────────────────────────────────

def suggest(symbol: str, outlook: str):
    symbol = symbol.upper()
    today  = date.today()

    client = make_client()
    try:
        spot = get_spot(client, symbol)
        print(f"\n{'='*70}")
        print(f"  {symbol} TRADE SUGGESTIONS  —  {outlook.upper()} outlook")
        print(f"  {symbol} @ ${spot:.2f}   As of: {today}")
        print(f"{'='*70}")

        # ── Pick expirations ──────────────────────────────────────────────────
        expirations = get_expirations(client, symbol)
        from datetime import datetime as _dt
        def _to_date(e):
            if isinstance(e, date) and not isinstance(e, _dt): return e
            if isinstance(e, _dt): return e.date()
            return _dt.strptime(str(e), "%Y-%m-%d").date()
        exp_dates = [_to_date(e) for e in expirations]
        exp_dates   = sorted(set(exp_dates))

        # near: 20-35 DTE, far: 45-65 DTE
        near_exp = next((e for e in exp_dates if 20 <= (e - today).days <= 35), None)
        far_exp  = next((e for e in exp_dates if 45 <= (e - today).days <= 65), None)

        if not near_exp:
            near_exp = next((e for e in exp_dates if (e - today).days >= 14), None)
        if not far_exp:
            far_exp = next((e for e in exp_dates if far_exp is None and (e - today).days > (near_exp - today).days + 10), None)

        print(f"\n  Using expirations: {near_exp} ({(near_exp-today).days} DTE)  /  {far_exp} ({(far_exp-today).days} DTE)")

        # ── Fetch chains ──────────────────────────────────────────────────────
        print("  Fetching option chains...", end="", flush=True)
        near_chain = get_chain(client, symbol, near_exp)
        far_chain  = get_chain(client, symbol, far_exp)
        print(" done.")

        # Filter to liquid options within ±20% of spot
        near_calls = filter_chain(near_chain, spot, "C", 0.90, 1.20)
        near_puts  = filter_chain(near_chain, spot, "P", 0.80, 1.05)
        far_calls  = filter_chain(far_chain,  spot, "C", 0.92, 1.18)

        # ── Batch fetch greeks ────────────────────────────────────────────────
        all_opts = near_calls + near_puts + far_calls
        # Deduplicate by OSI
        seen = set()
        unique_opts = []
        for o in all_opts:
            if o.osi not in seen:
                seen.add(o.osi)
                unique_opts.append(o)

        print(f"  Fetching greeks for {len(unique_opts)} options...", end="", flush=True)
        enrich_with_greeks(client, unique_opts)
        print(" done.")

        # Build maps for fast lookup
        near_call_map = {o.osi: o for o in near_calls}
        near_put_map  = {o.osi: o for o in near_puts}
        far_call_map  = {o.osi: o for o in far_calls}

        # ── Generate trade candidates ─────────────────────────────────────────
        all_trades = []

        if outlook in ("bullish",):
            all_trades += make_bull_call_spreads(near_calls, spot, near_exp, symbol)
            all_trades += make_bull_put_spreads(near_puts, spot, near_exp, symbol)
            all_trades += make_call_calendars(near_calls, far_calls, spot, near_exp, symbol)

        elif outlook in ("bearish",):
            # Mirror logic — bear put spread, bear call spread
            print("\n  (bearish suggestions coming soon — showing neutral for now)")
            all_trades += make_iron_condors(near_calls, near_puts, spot, near_exp, symbol)

        elif outlook in ("neutral",):
            all_trades += make_iron_condors(near_calls, near_puts, spot, near_exp, symbol)

        # ── Sort and display ──────────────────────────────────────────────────
        all_trades.sort(key=lambda t: t.score, reverse=True)

        # Group by strategy type
        def strategy_group(t):
            if "Put Spread" in t.name:    return 0  # highest PoP first for bullish
            if "Call Calendar" in t.name: return 1
            if "Bull Call" in t.name:     return 2
            return 3

        if outlook == "bullish":
            all_trades.sort(key=lambda t: (strategy_group(t), -t.score))

        print(f"\n  TOP {len(all_trades)} SUGGESTED TRADES\n")
        for i, trade in enumerate(all_trades, 1):
            display_trade(i, trade)

        print(f"\n{'='*70}\n")

    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Suggest options trades based on ticker and market outlook",
        epilog=(
            "Examples:\n"
            "  python3 suggest_trades.py NVDA --outlook bullish\n"
            "  python3 suggest_trades.py QQQ  --outlook neutral\n"
            "  python3 suggest_trades.py SPY  --outlook bearish"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("symbol",  help="Underlying ticker (e.g. NVDA, QQQ, SPX)")
    parser.add_argument("--outlook", choices=["bullish", "bearish", "neutral"],
                        default="bullish", help="Your market outlook (default: bullish)")
    args = parser.parse_args()

    try:
        suggest(args.symbol, args.outlook)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
