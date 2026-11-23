#!/usr/bin/env bash
# Apre l'app Ricerca a catena (finestra Electron; il server Python parte da solo).
set -euo pipefail
PROGETTO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ELECTRON="$PROGETTO/app/node_modules/.bin/electron"
if [ ! -x "$ELECTRON" ]; then
    echo "Electron non è installato. Esegui: cd $PROGETTO/app && npm install" >&2
    exit 1
fi
if [ ! -x "$PROGETTO/venv/bin/python" ]; then
    echo "Ambiente Python mancante. Esegui: cd $PROGETTO && uv venv venv && VIRTUAL_ENV=venv uv pip install -r requirements.txt" >&2
    exit 1
fi
cd "$PROGETTO/app"
exec "$ELECTRON" . "$@"
