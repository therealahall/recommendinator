#!/bin/bash
# Ollama entrypoint: starts server and pulls the configured models on first run.
#
# Model names come from the environment (set in docker-compose.yml or a .env
# file), NOT from config.yaml. The application's global config — including
# ollama.model — is database-backed and editable from the Settings page, so the
# config file is no longer a reliable source for these values and this container
# has no access to the database.
#
# These MUST agree with the `ollama.model`, `ollama.embedding_model`, and
# `ollama.conversation_model` settings the app requests. If you change any of
# them in the Settings page, set the matching OLLAMA_MODEL /
# OLLAMA_EMBEDDING_MODEL / OLLAMA_CONVERSATION_MODEL here and recreate this
# container, or the app will request a model that was never pulled.

set -euo pipefail

MODEL="${OLLAMA_MODEL:-mistral:7b}"
EMBEDDING_MODEL="${OLLAMA_EMBEDDING_MODEL:-nomic-embed-text}"
# Defaults to the generation model, matching the app: an empty
# ollama.conversation_model setting falls back to ollama.model. Set this only if
# you point the Settings page at a separate chat model, or chat will request a
# model that was never pulled.
CONVERSATION_MODEL="${OLLAMA_CONVERSATION_MODEL:-$MODEL}"

log() {
    echo "[entrypoint] $*"
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

log "Generation model: $MODEL"
log "Embedding model:  $EMBEDDING_MODEL"
log "Conversation model: $CONVERSATION_MODEL"

# Pull models if not already present.
# Escape the only regex metacharacter that appears in real model names ('.')
# so "llama3.2" doesn't false-match "llama3a2", while still anchoring to the
# start of the line — `ollama list` prints the model name as the first column,
# so anchoring prevents short names from substring-matching longer ones
# (e.g., MODEL=text incorrectly matching "nomic-embed-text").
# CONVERSATION_MODEL defaults to MODEL, so the already-downloaded check below
# makes the duplicate a no-op rather than a second pull.
for model_name in "$MODEL" "$EMBEDDING_MODEL" "$CONVERSATION_MODEL"; do
    if ollama list | grep -q "^${model_name//./\\.}"; then
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
