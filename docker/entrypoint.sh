#!/bin/sh
# Recommendinator container entrypoint.
#
# On first run, copies the bundled example.yaml to config.yaml inside the
# mounted /app/config volume and mints the API token the app refuses to start
# without. Idempotent — does not overwrite an existing config.yaml.
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
        # The app refuses to start without web.api_token, so a first run has to
        # mint one or the container never serves. Hex only, which is why it can
        # be substituted in without escaping anything.
        API_TOKEN=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
        sed -i "s|^\( *api_token: \)\"\"|\1\"$API_TOKEN\"|" "$CONFIG_PATH"
        echo "[entrypoint] No config.yaml found; copied example.yaml as a starting point."
        if grep -q "api_token: \"$API_TOKEN\"" "$CONFIG_PATH"; then
            # Printed once, on this run only, because the block is skipped as
            # soon as config.yaml exists.
            echo "[entrypoint] Minted an API token. Every API request must carry it, and the web UI asks for it once:"
            echo "[entrypoint]   $API_TOKEN"
            echo "[entrypoint] It is in ./config/config.yaml under web.api_token if you need it again."
        else
            echo "[entrypoint] WARNING: could not write web.api_token into config.yaml." >&2
            echo "[entrypoint] Set it by hand (openssl rand -hex 32) or the app will refuse to start." >&2
        fi
        # Do not advertise web.host/web.port here: the image's CMD passes
        # --host/--port, and CLI flags beat config.yaml — editing them in this
        # file under Docker changes nothing. Map the port with APP_PORT instead.
        echo "[entrypoint] Under Docker it carries only the storage paths, web.api_token and web.debug; the bind comes from --host/--port (set the published port with APP_PORT)."
        echo "[entrypoint] Data sources, settings, and API keys are managed in the app."
    else
        echo "[entrypoint] WARNING: neither config.yaml nor example.yaml present in $CONFIG_DIR." >&2
        echo "[entrypoint] The application may fail to start. Mount a config directory or rebuild the image." >&2
    fi
fi

exec "$@"
