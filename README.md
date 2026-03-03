# options-chain

## Overview

`options-chain` is a Public.com options analysis project with:

- Python scripts for quotes, chains, and account workflows
- a FastAPI backend that serves `GET /api/snapshot`
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
./run spx_spread_credit
```

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

Key query params:

- `symbol`: `SPX`, `QQQ`, `SPY`, `NDX`, `NVDA`, `TSLA`, `AAPL`, `MSFT`, `GOOGL`, `META`, `AMZN`, `IBIT`, `AVGO`
- `expiry_slot`: `0dte`, `next1`, `next2`
- legacy compatibility: `expiry_mode` + `dte`

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
