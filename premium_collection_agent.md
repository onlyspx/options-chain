# Premium Collection Agent — Vertical Spread Specification

## Overview
Build a **Premium Collection Agent** focused on **vertical credit spreads** using:
- Monthly / Weekly / Daily One-Time Framing (OTF)
- Expected Move
- Options chain filtering
- Defined-risk vertical spreads
- Structured management levels

Supported strategies:
- Call Credit Spreads (OTF Down)
- Put Credit Spreads (OTF Up)

---

## Core Workflow
1. Fetch real-time quote
2. Fetch historical OHLC data
3. Compute OTF (Monthly / Weekly / Daily)
4. Compute Expected Move
5. Determine strategy direction
6. Scan option chain
7. Build candidate spreads
8. Rank spreads
9. Define management levels
10. Output structured trade ideas

---

## OTF Definitions

### OTF Down
Each new bar high < previous bar high

Break condition:
Current high >= previous high

### OTF Up
Each new bar low > previous bar low

Break condition:
Current low <= previous low

Compute for:
- Monthly
- Weekly
- Daily

Return:
- state
- support/resistance
- break levels

---

## Expected Move

Formula:
expected_move = price * IV * sqrt(days/365)

Upper:
price + expected_move

Lower:
price - expected_move

---

## Strategy Direction

If weekly OTF down:
→ Sell Call Credit Spreads

If weekly OTF up:
→ Sell Put Credit Spreads

Else:
→ No trade / low confidence

---

## Call Credit Spread Selection

Aggressive:
short >= max(daily_resistance, upper_expected_move)

Balanced:
short >= max(weekly_resistance, upper_expected_move)

Conservative:
short >= max(monthly_resistance, upper_expected_move)

Spread:
Sell short strike
Buy short + width

---

## Put Credit Spread Selection

Aggressive:
short <= min(daily_support, lower_expected_move)

Balanced:
short <= min(weekly_support, lower_expected_move)

Conservative:
short <= min(monthly_support, lower_expected_move)

Spread:
Sell short strike
Buy short - width

---

## Spread Width
Default:
5

Configurable:
3 / 5 / 10

---

## Filtering Rules

- delta(short) <= 0.20
- credit >= minimum threshold
- OI >= minimum
- tight bid/ask
- acceptable risk/reward

---

## Risk Model

credit
max_loss = width - credit
breakeven
risk_reward = credit / max_loss

---

## Management Rules

### Alert
- price reaches expected move
- delta >= 0.25

### Defend
- daily OTF breaks
- spread value 1.5x credit

### Invalidation
- weekly OTF breaks
- short strike breached

---

## Output Example

Type: Call Credit Spread
Sell 690 / Buy 695
Credit: 0.32
Max Loss: 4.68

Why:
- Weekly OTF down
- Strike above expected move

Management:
Alert: 670
Defend: 675
Invalidation: 694

---

## Agent Flow Pseudocode

if weekly_otf == "down":
    strategy = "call_credit"
elif weekly_otf == "up":
    strategy = "put_credit"
else:
    strategy = "none"

---

## Modules

- data_provider
- otf_engine
- expected_move_engine
- spread_selector
- risk_engine
- broker_connector
- skill_interface

---

## v1 Scope

- Call credit spreads
- Put credit spreads
- Fixed width
- Top 3 ideas
- Analysis only mode
