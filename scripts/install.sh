#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=queue-proxy-common.sh
source "$SCRIPT_DIR/queue-proxy-common.sh"

require_root
require_systemctl
require_sources
require_safe_dropin

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
