# analyze_optionstrat.py

Parse an OptionsStrat URL and fetch live price, greeks, and risk metrics from the Public.com API.

## Usage

```bash
python3 scripts/analyze_optionstrat.py "<optionstrat-url>"
```

## Example

```bash
python3 scripts/analyze_optionstrat.py \
  "https://optionstrat.com/build/calendar-call-spread/SPX/-.SPXW260408C6590,.SPXW260422C6590"
```

Output:

```
========================================================================
  OPTIONSTRAT ANALYSIS  —  Calendar Call Spread
  Underlying : SPX     As of: 2026-03-25
========================================================================

  SYMBOL                      DIR          EXP      BID      ASK      MID      IV
  SPXW260408C06590000          -1   2026-04-08  $128.90  $129.80  $129.35   22.4%
  SPXW260422C06590000          +1   2026-04-22  $173.00  $174.20  $173.60   21.2%

  Field                        Our Script   OptionsStrat Free
  Net Debit                       $44.25             visible
  Max Loss                        $44.25             visible
  Max Profit                     $497.40             visible
  Breakevens               $6,412 – $6,803             visible
  Chance of Profit                  49.1%              LOCKED
  Delta                              0.42              LOCKED
  Theta ($/day)                      1.36              LOCKED
  Gamma                           -0.0400              LOCKED
  Vega                             211.40              LOCKED
  Rho                              130.06              LOCKED
========================================================================
```

## What it computes

| Field | How |
|---|---|
| Bid / Ask / Mid | Live from Public.com quotes API |
| IV | From Public.com greeks API |
| Delta, Theta, Gamma, Vega, Rho | From Public.com greeks API × 100 (contract multiplier) |
| Net Greeks | Weighted sum across legs by quantity (+1 long, -1 short) |
| Net Debit / Credit | Sum of (qty × mid × 100) across legs |
| Max Loss | Net debit paid (for debit spreads) |
| Max Profit | Peak P&L from Black-Scholes scan at short expiry |
| Breakevens | Zero-crossings of P&L curve at short expiry (BS model) |
| Chance of Profit | Log-normal probability of underlying between breakevens |

## Supported URL format

The script accepts **full OptionsStrat build URLs** only:

```
https://optionstrat.com/build/{strategy}/{underlying}/{legs}
```

Where legs are comma-separated, with `-` prefix for short positions:

```
-.SPXW260408C6590,.SPXW260422C6590
 ↑ short            ↑ long
```

Short share links (`optionstrat.com/AbCd1234`) are **not supported** — copy the full URL from your browser address bar.

## Supported strategies

Any multi-leg strategy works: calendar spreads, vertical spreads, iron condors, butterflies, etc. The script is leg-agnostic — it parses however many legs are in the URL.

## OSI symbol conversion

OptionsStrat symbols (`.SPXW260408C6590`) are automatically converted to OSI format (`SPXW260408C06590000`) for the Public.com API:

```
.SPXW260408C6590
   SPXW   = root
   260408 = YYMMDD expiry
   C      = call (P = put)
   6590   = strike → padded to 8 digits as 06590000
```

## Requirements

```
PUBLIC_COM_SECRET=<your-api-key>
PUBLIC_COM_ACCOUNT_ID=<your-account-id>
```

Set in `.env` at the repo root or as environment variables.
