"""
settings.py - reads private settings from environment variables or a .env file next to server.py.

.env (gitignored) looks like:
    GEMINI_API_KEY=your-key
    NTFY_TOPIC=stillwave-something-random
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))


def get(name):
    value = os.environ.get(name, "").strip()
    if value:
        return value
    path = os.path.join(HERE, ".env")
    if not os.path.isfile(path):
        return ""
    raw = open(path, "rb").read()
    # Notepad / PowerShell may save UTF-16 or add a BOM
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig", "ignore")
    for line in text.splitlines():
        key, _, val = line.partition("=")
        if key.strip() == name:
            return val.strip().strip('"').strip("'")
    return ""
