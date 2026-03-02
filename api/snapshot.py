"""
Vercel API entrypoint for multi-symbol dashboard snapshot data.

This endpoint reuses the existing application logic from web.server.main.
"""

from fastapi import FastAPI

from web.server.main import get_snapshot

app = FastAPI(title="Multi-Symbol Snapshot API")


@app.get("/")
@app.get("/api/snapshot")
def snapshot(
    mark_last_min: int | None = None,
    dte: int = 0,
    symbol: str = "SPX",
    expiry_mode: str = "dte",
    expiry_slot: str | None = None,
    strike_depth: str | None = None,
    include_skew: bool = False,
):
    """Return snapshot with optional symbol/expiry selectors and mark-last delta window."""
    return get_snapshot(
        mark_last_min=mark_last_min,
        dte=dte,
        symbol=symbol,
        expiry_mode=expiry_mode,
        expiry_slot=expiry_slot,
        strike_depth=strike_depth,
        include_skew=include_skew,
    )
