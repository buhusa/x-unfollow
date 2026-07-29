#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_MARKER="$VENV_DIR/.x-unfollow-ready"
APP_DATA_DIR="${X_UNFOLLOW_HOME:-$SCRIPT_DIR/user-data}"
LEGACY_DATA_DIR="$HOME/.x-unfollow"

cd "$SCRIPT_DIR"

if [[ ! -e "$APP_DATA_DIR" && -d "$LEGACY_DATA_DIR" ]]; then
  echo "Copying existing app data to: $APP_DATA_DIR"
  mkdir -p "$APP_DATA_DIR"
  cp -R "$LEGACY_DATA_DIR/." "$APP_DATA_DIR/"
  chmod 700 "$APP_DATA_DIR"
  if [[ -f "$APP_DATA_DIR/tokens.json" ]]; then
    chmod 600 "$APP_DATA_DIR/tokens.json"
  fi
fi

export X_UNFOLLOW_HOME="$APP_DATA_DIR"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: Python 3.11 or newer is required."
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "Error: Python 3.11 or newer is required."
  exit 1
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Creating local Python environment..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if [[ ! -f "$INSTALL_MARKER" || "$SCRIPT_DIR/pyproject.toml" -nt "$INSTALL_MARKER" ]]; then
  echo "Installing x-unfollow..."
  "$VENV_DIR/bin/python" -m pip install -e "$SCRIPT_DIR"
  touch "$INSTALL_MARKER"
fi

exec "$VENV_DIR/bin/x-unfollow"
