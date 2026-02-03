import argparse
import os
import sys

import requests


def get_token(minutes):
    # Retrieve secret from environment variable managed by OpenClaw
    secret = os.getenv("PUBLIC_COM_SECRET")
    
    if not secret:
        print("Error: PUBLIC_COM_SECRET is not set.")
        sys.exit(1)

    url = "https://api.public.com/userapiauthservice/personal/access-tokens"
    headers = {"Content-Type": "application/json"}
    request_body = {
        "validityInMinutes": minutes,
        "secret": secret
    }

    try:
        response = requests.post(url, headers=headers, json=request_body)
        response.raise_for_status()
        data = response.json()
        
        # OpenClaw reads the stdout (print) to relay info to the user
        print(f"Successfully generated token: {data.get('token')}")
        print(f"Expires in: {minutes} minutes")
    except Exception as e:
        print(f"Failed to fetch token: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=60)
    args = parser.parse_args()
    get_token(args.minutes)