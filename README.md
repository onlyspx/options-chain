# options-chain

## Overview

`options-chain` is a Public.com options analysis project with:

- Python scripts for quotes, chains, and account workflows
- a FastAPI backend that serves `GET /api/snapshot` and `GET /api/straddle-monitor`
- a React frontend dashboard for multi-symbol options monitoring

## Local Setup

### Prerequisites

- Python 3.8+
- Node.js + npm (for frontend build)
- Public.com account and API key: [Public API settings](https://public.com/settings/v2/api)

### Environment Variables

Create `.env` in the repo root (or copy from `.env.example`):

- `PUBLIC_COM_SECRET` (required)
- `PUBLIC_COM_ACCOUNT_ID` (recommended)
- `DISCORD_WEBHOOK_URL` (required for daemon mode unless passed via CLI)
- `SUPABASE_URL` (optional for straddle history persistence)
- `SUPABASE_SERVICE_ROLE_KEY` (optional for straddle history persistence)

If you do not know your account ID yet, run:

```bash
./run get_accounts
```

Then set `PUBLIC_COM_ACCOUNT_ID` in `.env`.

## Run Locally

### Python scripts

From repo root:

```bash
./run get_quotes SPY QQQ
./run get_option_chain SPY
./run spx_volume_leaders
./run spx_volume_leaders --last-5-min
./run spx_volume_daemon
./run spx_volume_daemon --interval-min 1 --top 10
./run spx_spread_credit
./run capture_straddle_close --force
```

`spx_volume_daemon` runs only during market hours (Mon-Fri, 09:30-16:00 ET) and exits outside that window.
In daemon mode, `--top` applies per side (up to N calls and up to N puts per post).
Each daemon alert starts with SPX price, then per-side delta sections with merged `Δ` + `Vol` rows; `⭐` marks strikes that are in that side's overall top-5 by current volume.

Or run scripts directly from the venv:

```bash
.venv/bin/python3 scripts/get_quotes.py SPY QQQ
```

### Dashboard (web)

```bash
./run_web.sh
```

Open `http://localhost:8000`.

The backend serves:

- `GET /api/snapshot`
- `GET /api/straddle-monitor`

Key query params:

- `symbol`: `SPX`, `QQQ`, `SPY`, `NDX`, `NVDA`, `TSLA`, `AAPL`, `MSFT`, `GOOGL`, `META`, `AMZN`, `IBIT`, `AVGO`
- `expiry_slot`: `0dte`, `next1`, `next2`
- legacy compatibility: `expiry_mode` + `dte`

The straddle monitor route lives at `/straddle`.

If `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are configured, the straddle monitor can persist:

- 1-minute intraday history for 0DTE and 1DTE
- daily 4:00 PM ET close snapshots for the visible near-term expiries

## Deploy To Vercel

This repo is configured as a full-stack Vercel app:

- static frontend from `web/frontend`
- Python API endpoint from `api/snapshot.py`

### 1) Import repository

Import this repository into Vercel and keep the project root at the repository root.

### 2) Configure environment variables

In Vercel project settings, add:

- `PUBLIC_COM_SECRET`
- `PUBLIC_COM_ACCOUNT_ID`

Set them for the environments you use (for example, Production and Preview).

### 3) Deploy

Trigger a deployment and verify the app loads and fetches `/api/snapshot` successfully.

## Secret Safety

- Never commit `.env` or key files
- Keep secrets in local `.env` and Vercel environment variables only
- If a secret is exposed, rotate it immediately

## Disclaimer

For illustrative and informational purposes only. Not investment advice or recommendations.
