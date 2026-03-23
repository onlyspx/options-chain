#!/usr/bin/env python3
"""
SPX 0DTE high-volume strike daemon.

Runs in the foreground, computes rolling volume deltas on a configurable
minute window, and posts compact updates to a Discord webhook.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

try:
    from config import get_account_id, get_api_secret
except ImportError:
    from scripts.config import get_account_id, get_api_secret

try:
    from public_api_sdk import (
        InstrumentType,
        OptionChainRequest,
        OptionExpirationsRequest,
        OrderInstrument,
        PublicApiClient,
        PublicApiClientConfiguration,
    )
    from public_api_sdk.auth_config import ApiKeyAuthConfig
except ImportError:
    InstrumentType = None
    OptionChainRequest = None
    OptionExpirationsRequest = None
    OrderInstrument = None
    PublicApiClient = None
    PublicApiClientConfiguration = None
    ApiKeyAuthConfig = None


SYMBOL = "SPX"
ET_TZ = ZoneInfo("America/New_York")
MARKET_OPEN_MINUTE = 9 * 60 + 30
MARKET_CLOSE_MINUTE = 16 * 60
MAX_DISCORD_CONTENT = 1900


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Daemon mode: post SPX 0DTE high-volume strike deltas to Discord.",
        epilog=(
            "Examples:\n"
            "  %(prog)s --top 5\n"
            "  %(prog)s --interval-min 5 --top 5\n"
            "  %(prog)s --interval-min 1 --top 5\n"
            "  %(prog)s --discord-webhook-url https://discord.com/api/webhooks/..."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--interval-min",
        type=int,
        default=5,
        help="Delta window and post cadence in minutes (default: 5).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="Top N strikes per side (calls and puts) by delta volume (default: 5).",
    )
    parser.add_argument(
        "--expiration",
        help="Optional expiration override (YYYY-MM-DD). Defaults to 0DTE if available, else nearest.",
    )
    parser.add_argument(
        "--discord-webhook-url",
        help="Discord webhook override (falls back to DISCORD_WEBHOOK_URL env var).",
    )
    args = parser.parse_args(argv)

    if args.interval_min < 1:
        parser.error("--interval-min must be >= 1")
    if args.top < 1:
        parser.error("--top must be >= 1")
    return args


def interval_seconds(interval_min):
    return int(interval_min) * 60


def now_et():
    return datetime.now(ET_TZ)


def is_market_hours(current_time):
    """US/Eastern regular cash market window: Mon-Fri, 09:30 <= t < 16:00."""
    if current_time.tzinfo is None:
        raise ValueError("current_time must be timezone-aware")
    if current_time.weekday() >= 5:
        return False
    minute_of_day = current_time.hour * 60 + current_time.minute
    return MARKET_OPEN_MINUTE <= minute_of_day < MARKET_CLOSE_MINUTE


def _norm_exp(exp):
    if hasattr(exp, "strftime"):
        return exp.strftime("%Y-%m-%d")
    return str(exp)


def parse_osi_symbol(osi_symbol):
    """Parse OSI symbol strike from trailing 8 digits (strike * 1000)."""
    try:
        strike_str = str(osi_symbol)[-8:]
        return int(strike_str) / 1000
    except (ValueError, TypeError):
        return None


def safe_volume(value):
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def build_snapshot(rows):
    return {row["osi"]: safe_volume(row.get("volume")) for row in rows if row.get("osi")}


def compute_delta_rows(current_rows, previous_snapshot):
    delta_rows = []
    for row in current_rows:
        osi = row.get("osi")
        current_volume = safe_volume(row.get("volume"))
        prior_volume = safe_volume(previous_snapshot.get(osi, 0))
        delta = max(0, current_volume - prior_volume)
        delta_rows.append(
            {
                "osi": osi,
                "side": row.get("side"),
                "strike": row.get("strike"),
                "volume": current_volume,
                "delta": delta,
            }
        )
    delta_rows.sort(key=lambda item: item.get("delta", 0), reverse=True)
    return delta_rows


def _format_strike(value):
    if value is None:
        return "--"
    try:
        numeric = float(value)
        if numeric.is_integer():
            return str(int(numeric))
        return f"{numeric:.2f}"
    except (TypeError, ValueError):
        return str(value)


def _strike_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _delta_rank_key(row):
    strike = _strike_float(row.get("strike"))
    strike_key = strike if strike is not None else float("inf")
    return (
        -safe_volume(row.get("delta")),
        strike_key,
        str(row.get("osi") or ""),
    )


def _display_strike_sort_key(row, descending=False):
    strike = _strike_float(row.get("strike"))
    missing = 1 if strike is None else 0
    directional = 0 if strike is None else (-strike if descending else strike)
    return (
        missing,
        directional,
        -safe_volume(row.get("delta")),
        str(row.get("osi") or ""),
    )


def _select_side_rows(delta_rows, side, top, strike_desc=False):
    filtered = [
        row
        for row in delta_rows
        if str(row.get("side") or "").upper() == side and safe_volume(row.get("delta")) > 0
    ]
    filtered.sort(key=_delta_rank_key)
    selected = filtered[:top]
    selected.sort(key=lambda row: _display_strike_sort_key(row, descending=strike_desc))
    return selected


def _select_side_rows_by_volume(current_rows, side, top, strike_desc=False):
    filtered = [
        row
        for row in current_rows
        if str(row.get("side") or "").upper() == side and safe_volume(row.get("volume")) > 0
    ]
    filtered.sort(
        key=lambda row: (
            -safe_volume(row.get("volume")),
            _strike_float(row.get("strike")) if _strike_float(row.get("strike")) is not None else float("inf"),
            str(row.get("osi") or ""),
        )
    )
    selected = filtered[:top]
    selected.sort(key=lambda row: _display_strike_sort_key(row, descending=strike_desc))
    return selected


def _overlap_keys(rows):
    keys = set()
    for row in rows:
        side = str(row.get("side") or "").upper()
        strike = _strike_float(row.get("strike"))
        if side in {"CALL", "PUT"} and strike is not None:
            keys.add((side, strike))
    return keys


def _row_overlap_marker(row, overlap):
    side = str(row.get("side") or "").upper()
    strike = _strike_float(row.get("strike"))
    if side in {"CALL", "PUT"} and strike is not None and (side, strike) in overlap:
        return "⭐ "
    return ""


def _format_spx_price(value):
    if value is None:
        return "SPX --"
    try:
        return f"SPX {float(value):,.2f}"
    except (TypeError, ValueError):
        return "SPX --"


def fetch_spx_price(client):
    quotes = client.get_quotes([OrderInstrument(symbol=SYMBOL, type=InstrumentType.INDEX)])
    if not quotes:
        return None
    return getattr(quotes[0], "last", None)


def format_message(
    delta_rows,
    current_rows,
    timestamp_et,
    expiration,
    is_today,
    interval_min,
    top,
    spx_price=None,
    note=None,
):
    status = "0DTE" if is_today else "nearest fallback"
    lines = [
        (
            f"{_format_spx_price(spx_price)} | {timestamp_et.strftime('%Y-%m-%d %H:%M:%S %Z')} "
            f"| exp {expiration} ({status}) | window {interval_min}m"
        )
    ]
    if note:
        lines.append(note)

    delta_call_rows = _select_side_rows(delta_rows, side="CALL", top=top, strike_desc=False)
    delta_put_rows = _select_side_rows(delta_rows, side="PUT", top=top, strike_desc=True)
    overall_call_rows = _select_side_rows_by_volume(current_rows, side="CALL", top=5, strike_desc=False)
    overall_put_rows = _select_side_rows_by_volume(current_rows, side="PUT", top=5, strike_desc=True)

    overall_top_keys = _overlap_keys(overall_call_rows) | _overlap_keys(overall_put_rows)

    lines.append("")
    lines.append("📈 CALLS (Δ5m, strike ↑)")
    if delta_call_rows:
        for row in delta_call_rows:
            marker = _row_overlap_marker(row, overall_top_keys)
            lines.append(
                f"• {marker}🟢 {_format_strike(row.get('strike'))} "
                f"Δ{safe_volume(row.get('delta')):,} | Vol {safe_volume(row.get('volume')):,}"
            )
    else:
        lines.append(f"• ⚪ No call delta > 0 in last {interval_min} minute(s).")

    lines.append("")
    lines.append("📉 PUTS (Δ5m, strike ↓)")
    if delta_put_rows:
        for row in delta_put_rows:
            marker = _row_overlap_marker(row, overall_top_keys)
            lines.append(
                f"• {marker}🔴 {_format_strike(row.get('strike'))} "
                f"Δ{safe_volume(row.get('delta')):,} | Vol {safe_volume(row.get('volume')):,}"
            )
    else:
        lines.append(f"• ⚪ No put delta > 0 in last {interval_min} minute(s).")

    return "\n".join(lines)


def trim_content(content, max_len=MAX_DISCORD_CONTENT):
    if len(content) <= max_len:
        return content
    suffix = "\n... (truncated)"
    return content[: max_len - len(suffix)] + suffix


def build_webhook_payload(content):
    return {"content": trim_content(content)}


def post_discord_message(webhook_url, content, timeout_sec=15):
    payload = json.dumps(build_webhook_payload(content)).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            # Discord can reject default urllib UA from some environments.
            "User-Agent": "curl/8.7.1",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            status = getattr(response, "status", None)
            if status not in (200, 204):
                raise RuntimeError(f"Unexpected Discord status: {status}")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = "<unreadable>"
        raise RuntimeError(f"Discord webhook HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Discord webhook network error: {exc}") from exc


def redact_webhook_url(webhook_url):
    parsed = urllib.parse.urlparse(webhook_url)
    scheme = parsed.scheme or "https"
    host = parsed.netloc or "discord.com"
    return f"{scheme}://{host}/***"


def ensure_sdk_installed():
    if PublicApiClient is None:
        raise RuntimeError(
            "publicdotcom-py is not installed. Run './run get_quotes SPY' or 'pip install -r requirements.txt'."
        )


def resolve_expiration(client, explicit_expiration=None):
    today_et = now_et().date().isoformat()
    if explicit_expiration:
        return explicit_expiration, explicit_expiration == today_et

    request = OptionExpirationsRequest(
        instrument=OrderInstrument(symbol=SYMBOL, type=InstrumentType.INDEX)
    )
    response = client.get_option_expirations(request)
    expirations = getattr(response, "expirations", None) or []
    normalized = [_norm_exp(exp) for exp in expirations]
    if not normalized:
        raise RuntimeError("No SPX option expirations returned by API.")
    if today_et in normalized:
        return today_et, True
    return normalized[0], False


def fetch_chain_rows(client, expiration):
    request = OptionChainRequest(
        instrument=OrderInstrument(symbol=SYMBOL, type=InstrumentType.INDEX),
        expiration_date=expiration,
    )
    chain = client.get_option_chain(request)
    calls = getattr(chain, "calls", None) or []
    puts = getattr(chain, "puts", None) or []

    rows = []
    for option in calls:
        osi = option.instrument.symbol if hasattr(option, "instrument") else ""
        rows.append(
            {
                "osi": osi,
                "side": "CALL",
                "strike": parse_osi_symbol(osi),
                "volume": safe_volume(getattr(option, "volume", None)),
            }
        )
    for option in puts:
        osi = option.instrument.symbol if hasattr(option, "instrument") else ""
        rows.append(
            {
                "osi": osi,
                "side": "PUT",
                "strike": parse_osi_symbol(osi),
                "volume": safe_volume(getattr(option, "volume", None)),
            }
        )
    return rows


def run_daemon(args):
    secret = get_api_secret()
    account_id = get_account_id()
    webhook_url = args.discord_webhook_url or os.getenv("DISCORD_WEBHOOK_URL")

    if not secret:
        print("Error: PUBLIC_COM_SECRET is not set.")
        return 1
    if not account_id:
        print("Error: PUBLIC_COM_ACCOUNT_ID is not set.")
        return 1
    if not webhook_url:
        print("Error: DISCORD_WEBHOOK_URL is not set. Use env var or --discord-webhook-url.")
        return 1

    current_et = now_et()
    if not is_market_hours(current_et):
        print(
            f"Outside market hours at {current_et.strftime('%Y-%m-%d %H:%M:%S %Z')} "
            "(Mon-Fri 09:30-16:00 ET). Exiting."
        )
        return 0

    ensure_sdk_installed()

    print(f"Starting SPX volume daemon at {current_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Running every {args.interval_min} minute(s)")
    print(f"Posting to {redact_webhook_url(webhook_url)}")
    if args.expiration:
        print(f"Using expiration override: {args.expiration}")

    client = PublicApiClient(
        ApiKeyAuthConfig(api_secret_key=secret),
        config=PublicApiClientConfiguration(default_account_number=account_id),
    )

    try:
        expiration, is_today = resolve_expiration(client, explicit_expiration=args.expiration)
        baseline_rows = fetch_chain_rows(client, expiration)
        snapshot = build_snapshot(baseline_rows)
        print(
            f"Baseline captured for expiration {expiration} "
            f"({'0DTE' if is_today else 'nearest fallback'})."
        )

        while True:
            time.sleep(interval_seconds(args.interval_min))
            current_et = now_et()
            if not is_market_hours(current_et):
                print(
                    f"Outside market hours at {current_et.strftime('%Y-%m-%d %H:%M:%S %Z')}. Exiting."
                )
                return 0

            try:
                next_expiration, next_is_today = resolve_expiration(
                    client, explicit_expiration=args.expiration
                )
                if next_expiration != expiration:
                    expiration = next_expiration
                    is_today = next_is_today
                    baseline_rows = fetch_chain_rows(client, expiration)
                    snapshot = build_snapshot(baseline_rows)
                    baseline_delta_rows = compute_delta_rows(baseline_rows, snapshot)
                    spx_price = fetch_spx_price(client)
                    message = format_message(
                        delta_rows=baseline_delta_rows,
                        current_rows=baseline_rows,
                        timestamp_et=current_et,
                        expiration=expiration,
                        is_today=is_today,
                        interval_min=args.interval_min,
                        top=args.top,
                        spx_price=spx_price,
                        note=f"Expiration changed to {expiration}; baseline reset.",
                    )
                    post_discord_message(webhook_url, message)
                    print(
                        f"[{current_et.strftime('%H:%M:%S %Z')}] Expiration rolled to {expiration}; "
                        "posted baseline reset heartbeat."
                    )
                    continue

                current_rows = fetch_chain_rows(client, expiration)
                delta_rows = compute_delta_rows(current_rows, snapshot)
                spx_price = fetch_spx_price(client)
                message = format_message(
                    delta_rows=delta_rows,
                    current_rows=current_rows,
                    timestamp_et=current_et,
                    expiration=expiration,
                    is_today=is_today,
                    interval_min=args.interval_min,
                    top=args.top,
                    spx_price=spx_price,
                )
                post_discord_message(webhook_url, message)
                positive_count = len([row for row in delta_rows if safe_volume(row.get("delta")) > 0])
                print(
                    f"[{current_et.strftime('%H:%M:%S %Z')}] Posted heartbeat "
                    f"(positive deltas: {positive_count})."
                )
                snapshot = build_snapshot(current_rows)
            except Exception as cycle_error:
                print(f"[{current_et.strftime('%H:%M:%S %Z')}] Cycle error: {cycle_error}")
                continue
    except KeyboardInterrupt:
        print("\nStopped by user.")
        return 0
    finally:
        client.close()


def main():
    args = parse_args()
    code = run_daemon(args)
    sys.exit(code)


if __name__ == "__main__":
    main()
