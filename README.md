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

## Entering Your Public.com API Key

Once the skill has been installed, you will be asked to enter your Public.com API key on the first prompt related to it. For example, if you ask "How is my Public portfolio doing today?" you will be asked to configure the API key. This will persist so long as the key stays active.

Some requests will need an account number. You can ask the skill to list your different Public accounts to see the account numbers, and you can set one as your default by simply asking. This default and the API key will be used for subsequent requests, unless told otherwise.

## Example Prompts

- How is my portfolio doing today?
- Can you get me the options chain for Nvidia for options expiring tomorrow?
- Can you get me the current quotes for Apple, Google, and Microsoft?
- Can you get my account history and list out and the deposits I've made?
- Set up a job to monitor the price of Bitcoin every 30 minutes. If the price is below $75K, buy $100 worth of it. If you are in a position and the price goes above $80K, sell it. All orders are market orders and only be in one position at a time. Run indefinitely.
- Get the options chain for Apple option contracts expiring Feb 18th. I want to open a call credit spread. Determine the best options contracts to do this with based on contract liquidity and max premium for cost.
