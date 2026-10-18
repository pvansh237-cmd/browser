"""
agent.py — console version. Run on the laptop whose screen you want to share.

    pip install -r requirements.txt
    python agent.py --server wss://YOUR-APP.onrender.com
(or put the link in server_url.txt next to this file)
"""
import argparse
import asyncio
import sys

import settings
from agent_core import run_agent


def on_pin_ready(code):
    print("=" * 50)
    print(" SCREEN SHARE AGENT")
    print("=" * 50)
    print(f" PIN: {code}")
    print(" Open the website on the other laptop and enter this PIN.")
    print("=" * 50)


def on_status(text):
    print(f"[status] {text}")


async def main(server_url):
    await run_agent(server_url, on_pin_ready, on_status, asyncio.Event())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default=None)
    args = parser.parse_args()
    url = settings.normalize(args.server) if args.server else settings.get_server_url()
    if settings.is_placeholder(url):
        print("Server URL not set. Run:  python agent.py --server wss://YOUR-APP.onrender.com")
        print("or write your link into server_url.txt next to agent.py")
        sys.exit(1)
    print("Server:", url)
    try:
        asyncio.run(main(url))
    except KeyboardInterrupt:
        print("Stopped.")
