#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3 is required. Install Python 3.10 or later, then run this script again." >&2
  exit 2
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10 or later is required." >&2
  exit 2
fi

cd "$PROJECT_DIR"
exec "$PYTHON" quota_lab.py "$@"
