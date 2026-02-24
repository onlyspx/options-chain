"""
Vercel FastAPI entrypoint.

Vercel auto-detects api/main.py and expects an exported `app`.
"""

from fastapi import FastAPI

from web.server.main import get_snapshot

app = FastAPI(title="SPX Snapshot API")


@app.get("/")
def health():
    return {"ok": True}


@app.get("/snapshot")
def snapshot(mark_last_min: int | None = None):
    """Return SPX snapshot with optional mark-last delta window."""
    return get_snapshot(mark_last_min=mark_last_min)
