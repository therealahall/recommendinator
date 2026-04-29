#!/bin/sh
# Recommendinator container entrypoint.
#
# On first run, copies the bundled example.yaml to config.yaml inside the
# mounted /app/config volume so the application has a working starting point.
# Idempotent — does not overwrite an existing config.yaml.
#
# This is a temporary affordance: once configuration moves into the application
# itself, this script (and the example.yaml it copies) goes away.

set -eu

: "${CONFIG_DIR:=/app/config}"

# Defense-in-depth: refuse CONFIG_DIR values outside the application tree.
# Inside Docker this is always /app/config; the env override exists only for
# unit tests, which run against pytest's tmp_path. Anything else is a misuse.
case "$CONFIG_DIR" in
    /app/* | /tmp/*) ;;
    *)
        echo "[entrypoint] FATAL: CONFIG_DIR must be under /app or /tmp; got: $CONFIG_DIR" >&2
        exit 1
        ;;
esac

CONFIG_PATH="$CONFIG_DIR/config.yaml"
EXAMPLE_PATH="$CONFIG_DIR/example.yaml"

if [ ! -f "$CONFIG_PATH" ]; then
    if [ -f "$EXAMPLE_PATH" ]; then
        cp "$EXAMPLE_PATH" "$CONFIG_PATH"
        echo "[entrypoint] No config.yaml found; copied example.yaml as a starting point."
        echo "[entrypoint] Edit ./config/config.yaml on the host with your settings, then restart."
    else
        echo "[entrypoint] WARNING: neither config.yaml nor example.yaml present in $CONFIG_DIR." >&2
        echo "[entrypoint] The application may fail to start. Mount a config directory or rebuild the image." >&2
    fi
fi

exec "$@"
