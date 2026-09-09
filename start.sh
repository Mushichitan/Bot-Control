#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m pip install --break-system-packages -r backend/requirements.txt >/tmp/cbc-pip.log 2>&1 || true
if [ -f frontend/package.json ]; then
  if [ ! -d frontend/node_modules ]; then
    (cd frontend && npm install)
  fi
  if [ ! -f frontend/dist/index.html ]; then
    (cd frontend && npm run build)
  fi
fi
export CBC_PORT="${CBC_PORT:-8787}"
exec python3 run.py --host 0.0.0.0 --port "${CBC_PORT}" --no-browser
