#!/bin/bash
# Ollama entrypoint: starts server and pulls models from config on first run.
#
# Reads model names from /config/config.yaml (mounted from host).
# Falls back to defaults if config is missing or unparseable.

set -euo pipefail

DEFAULT_MODEL="mistral:7b"
DEFAULT_EMBEDDING_MODEL="nomic-embed-text"

CONFIG_FILE="/config/config.yaml"

log() {
    echo "[entrypoint] $*"
}

# Parse a simple "key: value" from the ollama section of config.yaml.
# This handles quoted and unquoted values. Falls back to the provided default.
parse_config_value() {
    local key="$1"
    local default="$2"

    if [ ! -f "$CONFIG_FILE" ]; then
        echo "$default"
        return
    fi

    # Extract value after "key:" within the ollama config block.
    # Handles: model: mistral:7b / model: "mistral:7b" / embedding_model: "nomic-embed-text"
    local value
    value=$(sed -n '/^ollama:/,/^[^ ]/p' "$CONFIG_FILE" \
        | grep -E "^\s+${key}:" \
        | head -1 \
        | sed 's/^[^:]*:\s*//' \
        | sed 's/^"//' \
        | sed 's/"$//' \
        | sed 's/\s*#.*//' \
        | xargs)

    if [ -n "$value" ]; then
        echo "$value"
    else
        echo "$default"
    fi
}

# Pull a model with periodic progress logging.
# ollama pull's progress bars use carriage returns that don't render in docker logs,
# so we parse the output and log size updates periodically.
pull_model() {
    local model_name="$1"
    local last_logged=""

    log "Pulling $model_name — this may take several minutes on first run..."

    # Stream pull output line-by-line, logging meaningful progress updates.
    # ollama pull outputs lines like "pulling abc123... 45% ▕███    ▏ 1.2 GB/4.1 GB"
    ollama pull "$model_name" 2>&1 | while IFS= read -r line; do
        # Extract percentage if present
        if echo "$line" | grep -qE '[0-9]+%'; then
            percent=$(echo "$line" | grep -oE '[0-9]+%' | tail -1)
            # Log at 5% increments
            percent_num=${percent%%%}
            if [ "$((percent_num % 5))" -eq 0 ] && [ "$percent" != "$last_logged" ]; then
                log "  $model_name: $percent downloaded"
                last_logged="$percent"
            fi
        # Log non-progress lines (e.g., "verifying sha256 digest", "writing manifest")
        elif echo "$line" | grep -qiE 'verifying|writing|success'; then
            log "  $model_name: $line"
        fi
    done

    log "Model $model_name is ready."
}

# Redirect ollama server logs to a file so they don't drown out entrypoint output
log "Starting Ollama server..."
ollama serve > /tmp/ollama-server.log 2>&1 &
OLLAMA_PID=$!

log "Waiting for Ollama server to become responsive..."
wait_seconds=0
until ollama list > /dev/null 2>&1; do
    if ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
        log "ERROR: Ollama server failed to start. Server log:"
        cat /tmp/ollama-server.log
        exit 1
    fi
    wait_seconds=$((wait_seconds + 1))
    if [ $((wait_seconds % 5)) -eq 0 ]; then
        log "  Still waiting for server... (${wait_seconds}s)"
    fi
    sleep 1
done
log "Ollama server is ready (started in ${wait_seconds}s)."

# Read models from config
if [ -f "$CONFIG_FILE" ]; then
    log "Reading model config from $CONFIG_FILE"
else
    log "Config file not found at $CONFIG_FILE, using defaults."
fi

MODEL=$(parse_config_value "model" "$DEFAULT_MODEL")
EMBEDDING_MODEL=$(parse_config_value "embedding_model" "$DEFAULT_EMBEDDING_MODEL")

log "Generation model: $MODEL"
log "Embedding model:  $EMBEDDING_MODEL"

# Pull models if not already present
for model_name in "$MODEL" "$EMBEDDING_MODEL"; do
    if ollama list | grep -q "^${model_name}"; then
        log "Model $model_name is already downloaded."
    else
        pull_model "$model_name"
    fi
done

log "All models ready. Ollama is running (PID $OLLAMA_PID)."

# Signal that models are pulled and ready for use
touch /tmp/models-ready

# Wait for the server process
wait "$OLLAMA_PID"
