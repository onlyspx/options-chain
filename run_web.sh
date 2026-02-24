#!/usr/bin/env bash
# Run SPX 0DTE dashboard on your Mac: backend + frontend at http://localhost:8000
# From repo root. Uses .venv and .env.
set -e
cd "$(dirname "$0")"
if [[ ! -d .venv ]]; then
  echo "Creating venv and installing deps..."
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt -q
fi
# Build frontend so the server can serve it
if [[ -d web/frontend ]]; then
  (cd web/frontend && npm ci --silent 2>/dev/null || npm install --silent && npm run build)
fi
echo "Starting server at http://localhost:8000"
(sleep 2 && open "http://localhost:8000" 2>/dev/null) &
.venv/bin/uvicorn web.server.main:app --reload --host 0.0.0.0 --port 8000
