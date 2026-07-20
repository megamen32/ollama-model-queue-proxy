#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/install.py" ]; then
    exec python3 "$SCRIPT_DIR/install.py" "$@"
fi

RAW_BASE="${OLLAMA_QUEUE_RAW_BASE:-https://raw.githubusercontent.com/megamen32/ollama-model-queue-proxy/main}"
exec curl -fsSL "$RAW_BASE/scripts/install.py" | python3 - "$@"
