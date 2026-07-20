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

if [ -f "$SCRIPT_DIR/queue-proxy-common.sh" ] && [ -f "$SCRIPT_DIR/../ollama_model_queue_proxy.py" ]; then
    # shellcheck source=queue-proxy-common.sh
    source "$SCRIPT_DIR/queue-proxy-common.sh"
else
    [ "$(id -u)" -eq 0 ] || {
        printf '%s\n' 'error: run the piped installer with sudo' >&2
        exit 1
    }
    command -v curl >/dev/null 2>&1 || {
        printf '%s\n' 'error: curl is required for remote installation' >&2
        exit 1
    }
    BOOTSTRAP_DIR="$(mktemp -d)"
    mkdir -p "$BOOTSTRAP_DIR/scripts" "$BOOTSTRAP_DIR/systemd"
    curl -fsSL --retry 3 "$RAW_BASE/scripts/queue-proxy-common.sh" \
        -o "$BOOTSTRAP_DIR/scripts/queue-proxy-common.sh"
    curl -fsSL --retry 3 "$RAW_BASE/ollama_model_queue_proxy.py" \
        -o "$BOOTSTRAP_DIR/ollama_model_queue_proxy.py"
    curl -fsSL --retry 3 "$RAW_BASE/systemd/ollama-model-queue-proxy.service" \
        -o "$BOOTSTRAP_DIR/systemd/ollama-model-queue-proxy.service"
    # shellcheck source=queue-proxy-common.sh
    source "$BOOTSTRAP_DIR/scripts/queue-proxy-common.sh"
fi

require_port_change() {
    case "${CHANGE_PORT:-TRUE}" in
        TRUE|true|True|1|yes|YES|on|ON) ;;
        *) die 'this installer requires CHANGE_PORT=TRUE' ;;
    esac
}

require_root
require_systemctl
require_sources
require_safe_dropin
require_port_change

if ! "$SYSTEMCTL_BIN" cat "$OLLAMA_SERVICE_NAME" >/dev/null 2>&1; then
    die "$OLLAMA_SERVICE_NAME was not found; install Ollama first"
fi

mkdir -p "$LIBEXEC_DIR" "$DROPIN_DIR"
install -m 0755 "$PROXY_SOURCE" "$PROXY_TARGET"
render_proxy_unit

cat > "$DROPIN_FILE" <<EOF
$DROPIN_MARKER
# This moves Ollama behind the queue proxy. uninstall.sh removes only this file.
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11435"
EOF

"$SYSTEMCTL_BIN" daemon-reload
"$SYSTEMCTL_BIN" enable "$OLLAMA_SERVICE_NAME"
"$SYSTEMCTL_BIN" restart "$OLLAMA_SERVICE_NAME"
"$SYSTEMCTL_BIN" is-active --quiet "$OLLAMA_SERVICE_NAME"
"$SYSTEMCTL_BIN" enable --now "$PROXY_SERVICE_NAME"
"$SYSTEMCTL_BIN" is-active --quiet "$PROXY_SERVICE_NAME"

printf '%s\n' "Installed $PROXY_SERVICE_NAME."
printf '%s\n' 'Ollama backend: 127.0.0.1:11435'
printf '%s\n' 'Queue proxy:    127.0.0.1:11434'
printf '%s\n' 'Remove:         sudo ./scripts/uninstall.sh'
printf '%s\n' 'Remote remove:  curl -fsSL https://raw.githubusercontent.com/megamen32/ollama-model-queue-proxy/main/scripts/uninstall.sh | sudo bash'
