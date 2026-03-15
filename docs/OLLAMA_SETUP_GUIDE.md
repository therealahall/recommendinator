# Ollama Setup Guide

Model recommendations and setup steps for the Recommendinator AI features.

## Recommended Models

### Text Generation: `qwen2.5:14b`

| | |
|---|---|
| **Size** | ~9 GB RAM |
| **Speed (CPU)** | ~5-8 tokens/sec |
| **Instruction Following** | Best-in-class for this parameter count |
| **Structured Output** | Excellent JSON + formatted text |
| **Personality/Creativity** | Strong — matches the conversational recommendation style |

**Why this model over alternatives:**

- **vs `mistral:7b`** (project default): The desired output quality requires nuanced personality, cross-referencing user history, structured multi-section responses, and emotional engagement. Mistral 7B produces notably more generic output. The jump from 7B to 14B is where recommendations go from "acceptable" to "good."

- **vs `qwen2.5:32b`**: Higher quality, but at ~2-3 tokens/sec on CPU a 2000-token response takes 10+ minutes. Not viable for the conversation feature.

- **vs `deepseek-r1`**: A reasoning model that "thinks" extensively before answering, burning tokens on chain-of-thought. Great for math/logic, overkill and slow for recommendation chat.

- **vs `llama3.3:8b`**: Faster but weaker at following complex system prompts and producing structured, personality-driven output.

### Embeddings: `nomic-embed-text`

| | |
|---|---|
| **Size** | ~274 MB RAM |
| **Purpose** | Semantic similarity vectors for ChromaDB |
| **Speed** | Fast even on CPU (~0.5-2 sec per item) |

Purpose-built for embeddings. No change needed from the project default.

### Alternatives Worth Testing

| Model | Size | Trade-off |
|---|---|---|
| `mistral-nemo:12b` | ~7 GB | Slightly faster, slightly lower quality than Qwen 14B |
| `qwen2.5:32b` | ~20 GB | Higher quality but ~2-3 tok/sec on CPU (batch only, not chat) |
| `llama3.1:8b` | ~5 GB | Much faster, lower quality — acceptable for quick iteration |

## Configuration

### config.yaml

```yaml
features:
  ai_enabled: true
  embeddings_enabled: true
  llm_reasoning_enabled: true

ollama:
  base_url: "http://ollama:11434"  # Docker service name; use localhost:11434 if running Ollama on host
  model: "qwen2.5:14b"
  embedding_model: "nomic-embed-text"
```

### Pull Models (Docker)

```bash
docker exec <ollama-container> ollama pull qwen2.5:14b
docker exec <ollama-container> ollama pull nomic-embed-text
```

## First-Time Setup Steps

### 1. Verify Ollama Can Serve Both Models

```bash
# List models
docker exec <ollama-container> ollama list

# Smoke test text generation
curl http://localhost:11434/api/generate -d '{"model":"qwen2.5:14b","prompt":"Hello","stream":false}'

# Smoke test embeddings
curl http://localhost:11434/api/embeddings -d '{"model":"nomic-embed-text","prompt":"test"}'
```

### 2. Enable Feature Flags

Set all three flags to `true` in `config/config.yaml` (see Configuration section above).

### 3. Re-sync to Generate Embeddings

Existing items in SQLite will not have embeddings in ChromaDB. A re-sync generates an embedding per item during the save loop.

```bash
python3.11 -m src.cli update --source all
```

**Time estimate**: With 500-2000 items and `nomic-embed-text` on CPU, each embedding takes ~0.5-2 seconds. Expect **5-60 minutes** depending on item count. The CLI prints progress every 10 items.

### 4. Verify Embeddings Landed in ChromaDB

```bash
python3.11 -c "
from pathlib import Path
from src.storage.vector_db import VectorDB
vdb = VectorDB(Path('data/chroma_db'))
print(f'Embeddings in ChromaDB: {vdb.collection.count()}')
"
```

The count should roughly match the total item count in the database.

### 5. Test Recommendations (CLI First)

Start with the CLI recommendation path before trying the conversation feature:

```bash
python3.11 -m src.cli recommend --type video_game --count 3
```

This exercises the full scoring pipeline including `SemanticSimilarityScorer`. With `llm_reasoning_enabled` on, it also calls `qwen2.5:14b` for natural language explanations — expect 30-60 seconds on CPU for the first response (model load + generation).

### 6. Test Conversation

Once recommendations work, try the chat interface (web UI or CLI). The first message will be slow (model loading), subsequent messages faster.

## Troubleshooting

### Ollama base_url

Since Ollama runs in Docker, ensure `ollama.base_url` points to the right address:
- App on host, Ollama in Docker with port mapping: `http://localhost:11434`
- Both containerized: use the Docker network address

### Slow First Response

The first request after Ollama starts (or after model eviction) includes model loading time. Subsequent requests reuse the loaded model and are faster.

### Out of Memory

If the system runs low on RAM during sync or inference:
- Close other applications
- Reduce embedding batch processing (items are processed one at a time by default)
- Consider `llama3.1:8b` as a lighter text generation model

## Dual Text Models

If `qwen2.5:14b` feels too slow for interactive chat, you can configure a separate `conversation_model` — a smaller model for fast chat alongside the 14B for recommendation reasoning. Set `ollama.conversation_model` in your config to a lighter model (e.g., `llama3.1:8b`). When empty, the default `ollama.model` is used for everything.

See [MODEL_RECOMMENDATIONS.md](MODEL_RECOMMENDATIONS.md) for dual-model configuration syntax and compact mode setup. Note that document's model suggestions are general defaults — the model choices in this guide are tuned for higher quality output.
