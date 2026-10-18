"""
settings.py — where the agent finds the server URL.

Order: command line (--server)  >  env SERVER_URL  >  server_url.txt (next to the exe/script)  >  DEFAULT_SERVER
For the .exe: just put your Render link in a text file called server_url.txt next to the exe. No rebuild needed.
"""
import os
import sys

DEFAULT_SERVER = "wss://browser-eu63.onrender.com"   # <-- put your own Render URL here (or use server_url.txt)
URL_FILE = "server_url.txt"


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def normalize(url):
    url = (url or "").strip().rstrip("/")
    if url.startswith("https://"):
        url = "wss://" + url[len("https://"):]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://"):]
    elif url and not url.startswith(("ws://", "wss://")):
        url = "wss://" + url
    return url


def get_server_url():
    env = os.environ.get("SERVER_URL")
    if env:
        return normalize(env)
    path = os.path.join(base_dir(), URL_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read().strip()
        if txt:
            return normalize(txt)
    except OSError:
        pass
    return normalize(DEFAULT_SERVER)


def save_server_url(url):
    try:
        with open(os.path.join(base_dir(), URL_FILE), "w", encoding="utf-8") as f:
            f.write(normalize(url))
    except OSError:
        pass


def viewer_link(ws_url):
    ws_url = normalize(ws_url)
    if ws_url.startswith("wss://"):
        return "https://" + ws_url[len("wss://"):]
    if ws_url.startswith("ws://"):
        return "http://" + ws_url[len("ws://"):]
    return ws_url


def is_placeholder(url):
    return (not url) or "CHANGE-ME" in url
