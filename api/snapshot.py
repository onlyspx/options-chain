"""
Vercel API entrypoint for SPX dashboard snapshot data.

This endpoint reuses the existing application logic from web.server.main.
"""

from fastapi import FastAPI

from web.server.main import get_snapshot

app = FastAPI(title="SPX Snapshot API")


@app.get("/")
@app.get("/api/snapshot")
def snapshot(mark_last_min: int | None = None):
    """Return SPX snapshot with optional mark-last delta window."""
    return get_snapshot(mark_last_min=mark_last_min)
