#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="$ROOT/.venv/bin/python"
LOG_DIR="$ROOT/data"
LOG="$LOG_DIR/daily.log"
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

mkdir -p "$LOG_DIR"
cd "$ROOT"

if [ ! -x "$VENV_PY" ]; then
  /bin/bash "$ROOT/scripts/start.sh" >/dev/null 2>&1 || true
fi

stamp() { date "+%Y-%m-%d %H:%M:%S"; }
echo "[$(stamp)] collect start" >>"$LOG"
set +e
"$VENV_PY" -m star_digest collect >>"$LOG" 2>&1
code=$?
set -e
echo "[$(stamp)] collect exit $code" >>"$LOG"
exit "$code"
