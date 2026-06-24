#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${SJMJ_ENV_FILE:-$HOME/.sjmj-ai/backend.env}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/apps/invoice-ocr/backend"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "$UV_BIN" ]]; then
  echo "missing uv binary: set UV_BIN=/absolute/path/to/uv in $ENV_FILE" >&2
  exit 1
fi
if [[ "$UV_BIN" != /* ]]; then
  echo "UV_BIN must be an absolute path: $UV_BIN" >&2
  exit 1
fi

SJMJ_HOST="${SJMJ_HOST:-127.0.0.1}"
SJMJ_PORT="${SJMJ_PORT:-8400}"
LOG_LEVEL="${LOG_LEVEL:-info}"

cd "$BACKEND_DIR"
exec "$UV_BIN" run uvicorn app.main:app \
  --host "$SJMJ_HOST" \
  --port "$SJMJ_PORT" \
  --log-level "$LOG_LEVEL"
