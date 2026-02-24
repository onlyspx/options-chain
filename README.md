# Public.com OpenClaw Skill

## Overview

This is the repo for the OpenClaw skill to interact with your Public.com brokerage account. You can get live quotes, place orders, get portfolio info, and more.

### Run locally (no OpenClaw)

From the **repo root**, use the `run` script so the correct venv and `.env` are used:

```bash
./run get_quotes SPY QQQ
./run get_accounts
./run get_option_chain SPY
./run spx_volume_leaders              # top volume now (SPX today's chain)
./run spx_volume_leaders --last-5-min # top volume in last 5 min (waits 5 min)
./run spx_spread_credit               # SPX call credit spreads (mark + range until mark > $0.20)
```

**SPX 0DTE Dashboard (web)** — Run the dashboard in your browser (Mac):

```bash
./run_web.sh
```

Then open http://localhost:8000. The server serves the React frontend and `GET /api/snapshot` (SPX chain, volume, OI). Uses the same `.env` (`PUBLIC_COM_SECRET`, `PUBLIC_COM_ACCOUNT_ID`). First run builds the frontend and may take a minute.

Or with the venv explicitly: `.venv/bin/python3 scripts/get_quotes.py SPY QQQ`. Put your API key and account ID in a `.env` file (see `.env.example`). The first time you run `./run`, it will create `.venv` and install dependencies if needed.

## Deploy To Vercel

This repo is configured to deploy as a full-stack Vercel app:
- Static frontend from `web/frontend`
- Python API endpoint at `GET /api/snapshot` from `api/snapshot.py`

### 1) Import your GitHub repo in Vercel

- In Vercel, click **Add New Project** and select this repository.
- Keep the project root at the repository root.
- `vercel.json` sets the frontend build command and output directory.

### 2) Set environment variables in Vercel

In **Project Settings -> Environment Variables**, add:
- `PUBLIC_COM_SECRET` (required)
- `PUBLIC_COM_ACCOUNT_ID` (required for `/api/snapshot`)

Add them to the environments you use (typically **Production** and **Preview**), then redeploy.

### 3) Deploy

- Trigger a deployment from Vercel (or push to your connected branch).
- Open your Vercel URL; the app should load and fetch data from `/api/snapshot`.

### Secret Safety

- Never commit `.env` or any key files. This repo already ignores `.env`, `*.pem`, and `*.key`.
- Store secrets only in Vercel environment variables (and local `.env` for local-only use).
- If a secret is ever exposed, rotate it immediately in Public.com and update Vercel.

## Disclaimer

For illustrative and informational purposes only. Not investment advice or recommendations.

We recommend running OpenClaw with this skill in as isolated of an instance as possible. If possible, test the integration on a new Public account as well.

## Before You Get Started

There are a few prerequisites needed to get started:

- **Python 3.8+** and **pip** — Required in your OpenClaw environment. The skill's scripts use the `publicdotcom-py` SDK which will be auto-installed on first run.
- **Public.com account** — Create one at https://public.com/signup
- **Public.com API key** — Once you create your Public.com brokerage account, get an API key at https://public.com/settings/v2/api
- **AI model API key** — During our testing we used Anthropic, but OpenClaw allows you to choose from a few of your liking
- **Chat interface** — We used Telegram during development and will show instructions for that. You can use any of the interfaces supported by OpenClaw

## Installing OpenClaw

To install OpenClaw, follow their instructions: https://openclaw.ai/

On macOS/Linux it is one command:

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

Follow the instructions for the setup wizard. This is where you will select the AI model you will use as well as the chat provider.

## Install the Public.com OpenClaw Skill

Once OpenClaw is installed, you should have the `npm` and `npx` commands available. The Public.com OpenClaw skill is located on ClawHub: https://www.clawhub.ai/tarricsookdeo/claw-skill-public-dot-com

You can install it with this command:

```bash
npx clawhub@latest install claw-skill-public-dot-com
```

## Configuration

This skill uses two environment variables:

| Variable | Required | Description |
|---|---|---|
| `PUBLIC_COM_SECRET` | Yes | Your Public.com API secret key |
| `PUBLIC_COM_ACCOUNT_ID` | No | Default account ID for all requests |

### How secrets are resolved

Each variable is looked up in order:

1. **Secure file** — `~/.openclaw/workspace/.secrets/public_com_secret.txt` (or `public_com_account.txt`)
2. **Environment variable** — `PUBLIC_COM_SECRET` / `PUBLIC_COM_ACCOUNT_ID`

### Setting your API key

The easiest way is via `openclaw config set`, which writes to the secure file location:

```bash
openclaw config set skills.publicdotcom.PUBLIC_COM_SECRET <YOUR_API_SECRET>
```

You can find your API secret at https://public.com/settings/v2/api. Alternatively, the skill will prompt you for it on first use (e.g. "How is my Public portfolio doing today?").

### Setting a default account ID

Some requests require an account ID. You can list your accounts first, then set a default:

```bash
openclaw config set skills.publicdotcom.PUBLIC_COM_ACCOUNT_ID <YOUR_ACCOUNT_ID>
```

This eliminates the need to specify `--account-id` on each command.

## Example Prompts

- How is my portfolio doing today?
- Can you get me the options chain for Nvidia for options expiring tomorrow?
- Can you get me the current quotes for Apple, Google, and Microsoft?
- Can you get my account history and list out and the deposits I've made?
- Set up a job to monitor the price of Bitcoin every 30 minutes. If the price is below $75K, buy $100 worth of it. If you are in a position and the price goes above $80K, sell it. All orders are market orders and only be in one position at a time. Run indefinitely.
- Get the options chain for Apple option contracts expiring Feb 18th. I want to open a call credit spread. Determine the best options contracts to do this with based on contract liquidity and max premium for cost.
