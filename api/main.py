"""Vercel FastAPI entrypoint reusing full local app routes."""

# Reuse the existing app that already serves:
# - GET /api/snapshot
# - static frontend at /
from web.server.main import app
