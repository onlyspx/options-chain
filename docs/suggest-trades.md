# suggest_trades.py

Analyze a live options chain for any ticker and generate ranked trade suggestions
with real greeks, risk metrics, and OptionsStrat links — based on your market outlook.

## Usage

```bash
python3 scripts/suggest_trades.py <SYMBOL> --outlook <bullish|bearish|neutral>
```

## Examples

```bash
# Bullish on NVDA
python3 scripts/suggest_trades.py NVDA --outlook bullish

# Neutral on QQQ (iron condors)
python3 scripts/suggest_trades.py QQQ --outlook neutral

# SPX with bullish lean
python3 scripts/suggest_trades.py SPX --outlook bullish
```

## Sample output (NVDA bullish)

```
======================================================================
  NVDA TRADE SUGGESTIONS  —  BULLISH outlook
  NVDA @ $178.72   As of: 2026-03-25

  Using expirations: 2026-04-17 (23 DTE)  /  2026-05-15 (51 DTE)

  #1  Bull Put Spread   167/162P  Apr17
       Sell 167P / Buy 162P
  ──────────────────────────────────────────────────────────────────
  Net CREDIT:     $  83.50   Max Profit:    $  83.50
  Max Loss:       $ 416.50   Breakeven(s):  $166.66
  PoP:              75.3%   R/R:           1:0.2
  Net Delta:          7.00   Net Theta/day: $  1.59   Vega:  -2.67
  OptionsStrat: https://optionstrat.com/build/bull-put-spread/NVDA/...

  #7  Bull Call Spread  180/200C  Apr17
       Buy 180C / Sell 200C
  ──────────────────────────────────────────────────────────────────
  Net DEBIT:      $ 525.00   Max Profit:    $1,475.00
  Max Loss:       $ 525.00   Breakeven(s):  $185.25
  PoP:              33.3%   R/R:           1:2.8
  Net Delta:         42.14   Net Theta/day: $ -9.94   Vega:  11.61
  OptionsStrat: https://optionstrat.com/build/bull-call-spread/NVDA/...
======================================================================
```

## What it fetches

1. **Spot price** — live quote from Public.com
2. **Option chains** — full chain for two expirations (near ~23 DTE, far ~51 DTE)
3. **Greeks** — batch-fetched for all liquid options within ±20% of spot (delta, theta, gamma, vega, IV)

All data is live from the Public.com API (no delays, no third-party scraping).

## Trade types by outlook

### Bullish
| Strategy | Description | Best for |
|---|---|---|
| **Bull Put Spread** | Sell OTM put, buy lower put. Collect credit. | High PoP, theta positive, NVDA stays above short put |
| **Bull Call Spread** | Buy ATM call, sell higher call. Pay debit. | Defined risk with directional upside target |
| **Call Calendar** | Sell near-term call, buy far-term call at same strike | Moderately bullish; earn theta + benefit from IV expansion |

### Neutral
| Strategy | Description |
|---|---|
| **Iron Condor** | Sell OTM call spread + sell OTM put spread. Profit if underlying stays in range. |

### Bearish
Bear put spreads and bear call spreads (coming soon).

## Scoring & ranking

Each trade is scored and ranked within its strategy group:

| Strategy | Scoring weight |
|---|---|
| Bull Put Spread | 50% PoP + 50% reward/risk |
| Bull Call Spread | 40% PoP + 60% reward/risk |
| Call Calendar | 50% PoP + 30% theta yield + 20% vega bonus |
| Iron Condor | 60% PoP + 40% reward/risk |

For **bullish** outlook, trades are grouped: put spreads first (highest PoP), then calendars, then call spreads.

## Expiration selection

The script auto-selects two expirations:
- **Near**: 20–35 DTE (front month, for spreads and calendar short leg)
- **Far**: 45–65 DTE (back month, for calendar long leg)

Falls back to the next available expirations if the target window is empty.

## Liquidity filters

Options are excluded from consideration if:
- Bid/ask spread > 30% of mid price
- Mid price < $0.05
- Strike outside ±20% of spot (calls) or ±15% (puts)

## Risk metrics explained

| Field | Definition |
|---|---|
| Net Debit | Cost to enter (positive) or credit received (negative) × 100 |
| Max Profit | Maximum P&L at expiry × 100 |
| Max Loss | Maximum loss at expiry × 100 |
| Breakeven | Underlying price where P&L = 0 at short expiry |
| PoP | Probability underlying is past breakeven at expiry (log-normal model using live IV) |
| R/R | Max Profit ÷ Max Loss |
| Net Delta | Dollar move per $1 move in underlying |
| Net Theta | Dollar decay per calendar day |
| Net Vega | Dollar change per 1% move in IV |

## Requirements

```
PUBLIC_COM_SECRET=<your-api-key>
PUBLIC_COM_ACCOUNT_ID=<your-account-id>
```

Set in `.env` at the repo root or as environment variables.
