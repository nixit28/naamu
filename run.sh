#!/usr/bin/env bash
# Namu: one command to set up (first run only) and launch the local dashboard.
#   ./run.sh                     -> http://127.0.0.1:8765
#   NAMU_PORT=9000 ./run.sh      -> different port
#   NAMU_DATA_DIR=/path ./run.sh -> keep the database + PDFs somewhere else
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "error: $PY not found. Install Python 3.10+ first." >&2
  exit 1
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "Creating virtual environment in $VENV ..."
  if ! "$PY" -m venv "$VENV" >/dev/null 2>&1; then
    # Some distros ship Python without ensurepip; build the venv without pip and
    # install into it with the system pip instead.
    rm -rf "$VENV"
    "$PY" -m venv --without-pip "$VENV"
  fi
fi

if ! "$VENV/bin/python" -c "import flask, pdfplumber" >/dev/null 2>&1; then
  echo "Installing requirements (first run only) ..."
  if "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
    "$VENV/bin/python" -m pip install --quiet -r requirements.txt
  else
    "$PY" -m pip --python "$VENV/bin/python" install --quiet -r requirements.txt
  fi
fi

echo "Starting Namu at http://127.0.0.1:${NAMU_PORT:-8765}  (local only, Ctrl+C to stop)"
exec "$VENV/bin/python" -m namu.app
