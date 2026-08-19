#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="$ROOT/.venv/bin/python"
PORT=8787
URL="http://127.0.0.1:${PORT}/"
UV_BIN="${HOME}/.local/bin/uv"
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

cd "$ROOT"

if [ ! -x "$VENV_PY" ]; then
  if [ -x "$UV_BIN" ]; then
    "$UV_BIN" venv --python 3.12 "$ROOT/.venv"
  else
    python3 -m venv "$ROOT/.venv"
  fi
fi

if ! "$VENV_PY" -c "import fastapi, uvicorn, httpx, bs4, openai, dotenv" >/dev/null 2>&1; then
  if [ -x "$UV_BIN" ]; then
    "$UV_BIN" pip install --python "$VENV_PY" -r "$ROOT/requirements.txt"
  else
    "$VENV_PY" -m pip install -r "$ROOT/requirements.txt"
  fi
fi

mkdir -p "$ROOT/data"
if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  nohup "$VENV_PY" -m uvicorn app:app --host 127.0.0.1 --port "$PORT" \
    >"$ROOT/data/server.log" 2>&1 &
  ready=0
  for _ in $(seq 1 40); do
    if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 0.4
  done
  if [ "$ready" -ne 1 ]; then
    echo "服务启动超时，请看 $ROOT/data/server.log" >&2
    exit 1
  fi
fi

open "$URL"
