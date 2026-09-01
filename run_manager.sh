#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$ROOT/logs"
export PYTHONPATH="${ROOT}/python${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1
export MT5_MANAGER_DAEMON=1
cd "$ROOT"
if command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=python
fi
exec "$PY" -u -m mt5_hook manage --interval 20 --no-sleep
