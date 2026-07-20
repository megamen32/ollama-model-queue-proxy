#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

SYSTEMD_DIR="${OLLAMA_QUEUE_SYSTEMD_DIR:-/etc/systemd/system}"
LIBEXEC_DIR="${OLLAMA_QUEUE_LIBEXEC_DIR:-/usr/local/libexec}"
SYSTEMCTL_BIN="${OLLAMA_QUEUE_SYSTEMCTL:-systemctl}"
OLLAMA_SERVICE_NAME="${OLLAMA_SERVICE_NAME:-ollama.service}"
PROXY_SERVICE_NAME="${PROXY_SERVICE_NAME:-ollama-model-queue-proxy.service}"
PROXY_SOURCE="$REPO_DIR/ollama_model_queue_proxy.py"
PROXY_UNIT_SOURCE="$REPO_DIR/systemd/ollama-model-queue-proxy.service"
PROXY_TARGET="$LIBEXEC_DIR/ollama_model_queue_proxy.py"
PROXY_UNIT_TARGET="$SYSTEMD_DIR/$PROXY_SERVICE_NAME"
DROPIN_DIR="$SYSTEMD_DIR/ollama.service.d"
DROPIN_FILE="$DROPIN_DIR/90-ollama-model-queue-proxy.conf"
DROPIN_MARKER="# Managed by ollama-model-queue-proxy install.sh"

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        die "run this command with sudo"
    fi
}

require_systemctl() {
    if [[ "$SYSTEMCTL_BIN" == */* ]]; then
        [ -x "$SYSTEMCTL_BIN" ] || die "systemctl executable not found: $SYSTEMCTL_BIN"
    elif ! command -v "$SYSTEMCTL_BIN" >/dev/null 2>&1; then
        die "systemctl is required"
    fi
}

require_sources() {
    [ -f "$PROXY_SOURCE" ] || die "proxy source not found: $PROXY_SOURCE"
    [ -f "$PROXY_UNIT_SOURCE" ] || die "systemd unit not found: $PROXY_UNIT_SOURCE"
}

require_safe_dropin() {
    if [ -e "$DROPIN_FILE" ] && ! grep -Fq "$DROPIN_MARKER" "$DROPIN_FILE"; then
        die "refusing to overwrite unmanaged drop-in: $DROPIN_FILE"
    fi
}

render_proxy_unit() {
    local temporary_unit

    mkdir -p "$SYSTEMD_DIR"
    if [ "$LIBEXEC_DIR" = "/usr/local/libexec" ]; then
        install -m 0644 "$PROXY_UNIT_SOURCE" "$PROXY_UNIT_TARGET"
        return
    fi

    case "$LIBEXEC_DIR" in
        *'|'*) die "OLLAMA_QUEUE_LIBEXEC_DIR cannot contain the | character" ;;
    esac

    temporary_unit="$(mktemp)"
    sed "s|/usr/local/libexec/ollama_model_queue_proxy.py|$PROXY_TARGET|g" \
        "$PROXY_UNIT_SOURCE" > "$temporary_unit"
    install -m 0644 "$temporary_unit" "$PROXY_UNIT_TARGET"
    rm -f "$temporary_unit"
}
