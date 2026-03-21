#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/srv/spx0/current}"
VENV_DIR="${VENV_DIR:-/srv/spx0/shared/venv}"
SERVICE_NAME="${SERVICE_NAME:-spx0-web.service}"
BRANCH_NAME="${BRANCH_NAME:-feat/spx0-prod}"
SKIP_GIT_SYNC="${SKIP_GIT_SYNC:-0}"

ensure_rollup_native() {
  local rollup_version=""
  local package_name=""
  local arch=""

  if [[ "$(uname -s)" != "Linux" ]]; then
    return 0
  fi

  arch="$(uname -m)"
  case "$arch" in
    x86_64)
      package_name="@rollup/rollup-linux-x64-gnu"
      ;;
    aarch64|arm64)
      package_name="@rollup/rollup-linux-arm64-gnu"
      ;;
    *)
      echo "Skipping explicit Rollup native package install for unsupported Linux arch: $arch"
      return 0
      ;;
  esac

  rollup_version="$(node -p "require('./package-lock.json').packages['node_modules/rollup'].version")"
  npm install --no-save "${package_name}@${rollup_version}"
}

if [[ ! -d "$APP_ROOT/.git" ]]; then
  echo "App root does not look like a git checkout: $APP_ROOT" >&2
  exit 1
fi

cd "$APP_ROOT"

if [[ "$SKIP_GIT_SYNC" != "1" ]]; then
  echo "Deploying branch $BRANCH_NAME in $APP_ROOT"
  git fetch --all --prune
  git checkout "$BRANCH_NAME"
  git pull --ff-only origin "$BRANCH_NAME"
else
  echo "Deploying current working tree in $APP_ROOT (git sync skipped)"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r requirements.txt

pushd web/frontend >/dev/null
npm install
ensure_rollup_native
npm run build
popd >/dev/null

if systemctl list-unit-files | grep -q "^$SERVICE_NAME"; then
  sudo systemctl restart "$SERVICE_NAME"
  sudo systemctl status "$SERVICE_NAME" --no-pager
else
  echo "systemd unit $SERVICE_NAME is not installed yet; skipping restart."
fi
