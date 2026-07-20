#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RAW_BASE="${OLLAMA_QUEUE_RAW_BASE:-https://raw.githubusercontent.com/megamen32/ollama-model-queue-proxy/main}"
BOOTSTRAP_DIR=""

cleanup_bootstrap() {
    if [ -n "$BOOTSTRAP_DIR" ]; then
        rm -rf "$BOOTSTRAP_DIR"
    fi
}

trap cleanup_bootstrap EXIT

if [ -f "$SCRIPT_DIR/queue-proxy-common.sh" ]; then
    # shellcheck source=queue-proxy-common.sh
    source "$SCRIPT_DIR/queue-proxy-common.sh"
else
    [ "$(id -u)" -eq 0 ] || {
        printf '%s\n' 'error: run the piped uninstaller with sudo' >&2
        exit 1
    }
    command -v curl >/dev/null 2>&1 || {
        printf '%s\n' 'error: curl is required for remote removal' >&2
        exit 1
    }
    BOOTSTRAP_DIR="$(mktemp -d)"
    mkdir -p "$BOOTSTRAP_DIR/scripts"
    curl -fsSL --retry 3 "$RAW_BASE/scripts/queue-proxy-common.sh" \
        -o "$BOOTSTRAP_DIR/scripts/queue-proxy-common.sh"
    # shellcheck source=queue-proxy-common.sh
    source "$BOOTSTRAP_DIR/scripts/queue-proxy-common.sh"
fi

require_root
require_systemctl
require_safe_dropin

"$SYSTEMCTL_BIN" disable --now "$PROXY_SERVICE_NAME" >/dev/null 2>&1 || true
rm -f "$PROXY_UNIT_TARGET" "$PROXY_TARGET"

if [ -f "$DROPIN_FILE" ]; then
    rm -f "$DROPIN_FILE"
fi
rmdir "$DROPIN_DIR" 2>/dev/null || true

"$SYSTEMCTL_BIN" daemon-reload
if "$SYSTEMCTL_BIN" cat "$OLLAMA_SERVICE_NAME" >/dev/null 2>&1; then
    "$SYSTEMCTL_BIN" enable "$OLLAMA_SERVICE_NAME"
    "$SYSTEMCTL_BIN" restart "$OLLAMA_SERVICE_NAME"
    "$SYSTEMCTL_BIN" is-active --quiet "$OLLAMA_SERVICE_NAME"
    printf '%s\n' 'Ollama restored on 127.0.0.1:11434.'
else
    printf '%s\n' "warning: $OLLAMA_SERVICE_NAME is not installed; proxy files were removed." >&2
fi

printf '%s\n' "Removed $PROXY_SERVICE_NAME."
