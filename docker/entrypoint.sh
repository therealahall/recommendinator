#!/bin/sh
# Recommendinator container entrypoint.
#
# On first run, copies the bundled example.yaml to config.yaml inside the
# mounted /app/config volume, 0600, for the operator to set web.api_token in.
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
        # Before the operator's token lands, not after: cp inherits
        # example.yaml's 0644, so on the bind-mounted ./config every user on
        # the host could read it. data/.credential_key is 0600 already.
        chmod 600 "$CONFIG_PATH"
        echo "[entrypoint] No config.yaml found; copied example.yaml as a starting point."
        # No token is minted here. A secret nobody chose, announced once in a
        # log line, is a secret nobody has, and only this path ever minted one:
        # a from-source install was told to look for something never written.
        echo "[entrypoint] Set web.api_token in ./config/config.yaml before starting: openssl rand -hex 32"
        echo "[entrypoint] None is generated for you, and the app refuses to start until you set one."
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
