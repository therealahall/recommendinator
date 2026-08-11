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
# Outside CONFIG_DIR on purpose — the host's ./config is bind-mounted over it,
# which hides anything the image ships there.
: "${SEED_CONFIG:=/app/example.yaml}"

# Defense-in-depth: refuse paths outside the application tree. Inside Docker
# both are under /app; the env overrides exist only for unit tests, which run
# against pytest's tmp_path. Anything else is a misuse.
require_in_tree() {
    case "$2" in
        /app/* | /tmp/*) ;;
        *)
            echo "[entrypoint] FATAL: $1 must be under /app or /tmp; got: $2" >&2
            exit 1
            ;;
    esac
}

require_in_tree CONFIG_DIR "$CONFIG_DIR"
require_in_tree SEED_CONFIG "$SEED_CONFIG"

CONFIG_PATH="$CONFIG_DIR/config.yaml"

if [ ! -f "$CONFIG_PATH" ]; then
    if [ -f "$SEED_CONFIG" ]; then
        cp "$SEED_CONFIG" "$CONFIG_PATH"
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
        echo "[entrypoint] WARNING: no config.yaml in $CONFIG_DIR and no seed at $SEED_CONFIG." >&2
        echo "[entrypoint] The application may fail to start. Mount a config directory or rebuild the image." >&2
    fi
fi

exec "$@"
