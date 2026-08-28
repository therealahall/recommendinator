#!/bin/sh
# Once configuration moves into the application itself, this script goes away.

set -eu

: "${CONFIG_DIR:=/app/config}"
# Outside CONFIG_DIR on purpose — the host's ./config is bind-mounted over it,
# which hides anything the image ships there.
: "${SEED_CONFIG:=/app/example.yaml}"

# Defense-in-depth: refuse paths outside the application tree. The env
# overrides exist only for unit tests, against pytest's tmp_path; anything else
# is a misuse. A prefix test alone accepts /app/../etc, which names /etc, so a
# `..` component disqualifies too.
require_in_tree() {
    in_tree=false
    case "$2" in
        /app/* | /tmp/*) in_tree=true ;;
    esac
    case "/$2/" in
        *"/../"*) in_tree=false ;;
    esac
    if [ "$in_tree" != true ]; then
        echo "[entrypoint] FATAL: $1 must be under /app or /tmp, with no '..'; got: $2" >&2
        exit 1
    fi
}

require_in_tree CONFIG_DIR "$CONFIG_DIR"
require_in_tree SEED_CONFIG "$SEED_CONFIG"

CONFIG_PATH="$CONFIG_DIR/config.yaml"

if [ ! -f "$CONFIG_PATH" ]; then
    if [ -f "$SEED_CONFIG" ]; then
        cp "$SEED_CONFIG" "$CONFIG_PATH"
        # cp inherits example.yaml's 0644, so on the bind-mounted ./config
        # every user on the host could read it. data/.credential_key is 0600
        # already.
        chmod 600 "$CONFIG_PATH"
        echo "[entrypoint] No config.yaml found; copied example.yaml as a starting point."
        # No secret is minted here, and none is needed: the first visitor to
        # the web UI creates the account, and nobody else can once they have.
        echo "[entrypoint] Open the web UI and create your account — until you do, whoever reaches it first can."
        # Do not advertise web.host/web.port here: the image's CMD passes
        # --host/--port, and CLI flags beat config.yaml — editing them in this
        # file under Docker changes nothing. Map the port with APP_PORT instead.
        echo "[entrypoint] Under Docker it carries only the storage paths and web.debug; the bind comes from --host/--port (set the published port with APP_PORT)."
        echo "[entrypoint] Data sources, settings, and API keys are managed in the app."
    else
        echo "[entrypoint] WARNING: no config.yaml in $CONFIG_DIR and no seed at $SEED_CONFIG." >&2
        echo "[entrypoint] The application may fail to start. Mount a config directory or rebuild the image." >&2
    fi
fi

exec "$@"
