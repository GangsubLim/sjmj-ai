#!/usr/bin/env bash
set -euo pipefail

LABEL="ai.sjmj.backend"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAPPER_PATH="$PROJECT_ROOT/scripts/run-backend.sh"
TEMPLATE_PATH="$PROJECT_ROOT/deploy/launchd/$LABEL.plist.template"
ENV_FILE="${SJMJ_ENV_FILE:-$HOME/.sjmj-ai/backend.env}"
STATE_DIR="$(dirname "$ENV_FILE")"
LOG_DIR="${SJMJ_LOG_DIR:-$STATE_DIR/logs}"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
GUI_DOMAIN="gui/$(id -u)"

if [[ ! -f "$TEMPLATE_PATH" ]]; then
  echo "missing template: $TEMPLATE_PATH" >&2
  exit 1
fi
if [[ ! -x "$WRAPPER_PATH" ]]; then
  echo "wrapper is not executable: $WRAPPER_PATH (run: chmod +x $WRAPPER_PATH)" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  echo "copy deploy/env/backend.env.example to $ENV_FILE first" >&2
  exit 1
fi

mkdir -p "$STATE_DIR" "$LOG_DIR" "$HOME/Library/LaunchAgents"
chmod 700 "$STATE_DIR" || echo "warn: chmod 700 failed for $STATE_DIR — verify perms manually" >&2
chmod 600 "$ENV_FILE" || echo "warn: chmod 600 failed for $ENV_FILE — verify perms manually" >&2

python3 - "$TEMPLATE_PATH" "$PLIST_PATH" "$WRAPPER_PATH" "$PROJECT_ROOT" "$ENV_FILE" "$LOG_DIR" <<'PY'
from pathlib import Path
import sys

template_path, plist_path, wrapper_path, project_root, env_file, log_dir = map(Path, sys.argv[1:])
content = template_path.read_text()
replacements = {
    "__WRAPPER_PATH__": str(wrapper_path),
    "__PROJECT_ROOT__": str(project_root),
    "__ENV_FILE__": str(env_file),
    "__LOG_DIR__": str(log_dir),
}
for old, new in replacements.items():
    content = content.replace(old, new)
Path(plist_path).write_text(content)
PY

plutil -lint "$PLIST_PATH"

launchctl bootout "$GUI_DOMAIN/$LABEL" 2>/dev/null || true

# bootout is asynchronous; wait for full unload so the following bootstrap does
# not race the socket release (otherwise launchctl returns "Input/output error 5").
for _ in $(seq 1 15); do
  launchctl print "$GUI_DOMAIN/$LABEL" >/dev/null 2>&1 || break
  sleep 1
done

# If still registered after the poll budget, warn but proceed (bootstrap retry handles it).
launchctl print "$GUI_DOMAIN/$LABEL" >/dev/null 2>&1 \
  && echo "warn: $LABEL still registered after 15s teardown poll; proceeding to bootstrap" >&2

# retry bootstrap to ride out transient I/O errors during teardown.
for attempt in $(seq 1 5); do
  if launchctl bootstrap "$GUI_DOMAIN" "$PLIST_PATH"; then
    break
  fi
  if [[ "$attempt" -eq 5 ]]; then
    echo "bootstrap failed after 5 attempts for $LABEL" >&2
    exit 1
  fi
  sleep 2
done

launchctl kickstart -k "$GUI_DOMAIN/$LABEL"

echo "installed and started $LABEL"
echo "plist: $PLIST_PATH"
echo "env:   $ENV_FILE"
echo "logs:  $LOG_DIR"
