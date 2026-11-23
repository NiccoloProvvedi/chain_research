#!/usr/bin/env bash
# Opens the Chain Research app (Electron window; the Python server starts on its own).
set -euo pipefail
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ELECTRON="$PROJECT/app/node_modules/.bin/electron"
if [ ! -x "$ELECTRON" ]; then
    echo "Electron is not installed. Run: cd $PROJECT/app && npm install" >&2
    exit 1
fi
if [ ! -x "$PROJECT/venv/bin/python" ]; then
    echo "Python environment missing. Run: cd $PROJECT && uv venv venv && VIRTUAL_ENV=venv uv pip install -r requirements.txt" >&2
    exit 1
fi
cd "$PROJECT/app"
exec "$ELECTRON" . "$@"
