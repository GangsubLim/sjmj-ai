#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${SJMJ_ENV_FILE:-$HOME/.sjmj-ai/ml-worker.env}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ML_DIR="$PROJECT_ROOT/apps/invoice-ocr/ml"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" || "$PYTHON_BIN" != /* ]]; then
  echo "PYTHON_BIN must be an absolute path to worker venv python" >&2
  exit 1
fi

cd "$ML_DIR"
exec "$PYTHON_BIN" -m worker.main
