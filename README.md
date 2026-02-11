# Public.com OpenClaw Skill

## Overview

This is the repo for the OpenClaw skill to interact with your Public.com brokerage account. You can get live quotes, place orders, get portfolio info, and more.

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
