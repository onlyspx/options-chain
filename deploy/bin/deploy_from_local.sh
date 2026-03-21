#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-deploy@5.78.139.5}"
REMOTE_APP_ROOT="${REMOTE_APP_ROOT:-/srv/spx0/current}"
REMOTE_VENV_DIR="${REMOTE_VENV_DIR:-/srv/spx0/shared/venv}"

rsync -az --delete \
  --exclude '.env' \
  --exclude '.venv' \
  --exclude 'node_modules' \
  --exclude 'web/frontend/node_modules' \
  --exclude 'web/frontend/dist' \
  ./ "${REMOTE_HOST}:${REMOTE_APP_ROOT}/"

ssh "$REMOTE_HOST" \
  "cd '${REMOTE_APP_ROOT}' && SKIP_GIT_SYNC=1 APP_ROOT='${REMOTE_APP_ROOT}' VENV_DIR='${REMOTE_VENV_DIR}' bash deploy/bin/redeploy.sh"
