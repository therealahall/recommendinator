# Ollama Setup Guide

Models and setup steps for Recommendinator's AI features. These picks are tuned
for output quality. [MODEL_RECOMMENDATIONS.md](MODEL_RECOMMENDATIONS.md) covers
the lighter general defaults.

## Recommended models

**Text generation: `qwen2.5:14b`.** Around 9 GB of RAM and 5-8 tokens/sec on
CPU. Best in class at that size for following instructions and emitting clean
JSON, and it holds the conversational tone the prompts ask for.

| Instead of | What you get |
|---|---|
| `mistral:7b` (project default) | Noticeably more generic output. 7B to 14B is where recommendations go from acceptable to good. |
| `qwen2.5:32b` | Better again, but 2-3 tok/sec on CPU means 10+ minutes for a 2000-token reply. Not usable for chat. |
| `deepseek-r1` | Burns tokens thinking before it answers. Great at math, slow and pointless here. |
| `mistral-nemo:12b` | Slightly faster, slightly lower quality than Qwen 14B. |
| `llama3.1:8b` | Much faster, lower quality. Fine while you iterate. |

**Embeddings: `nomic-embed-text`.** Around 274 MB and fast even on CPU, roughly
0.5-2 seconds per item. Purpose built, so keep the default.

## Configure

These live in the database. Use the **Settings** page or the `settings` CLI:

```bash
python3.11 -m src.cli settings set features.ai_enabled true
python3.11 -m src.cli settings set features.embeddings_enabled true
python3.11 -m src.cli settings set features.llm_reasoning_enabled true

# Docker service name. Use http://localhost:11434 if Ollama runs on the host.
python3.11 -m src.cli settings set ollama.base_url http://ollama:11434
python3.11 -m src.cli settings set ollama.model qwen2.5:14b
python3.11 -m src.cli settings set ollama.embedding_model nomic-embed-text
```

The three feature flags only take effect after a restart. The `ollama.*`
settings reach the next call without one.

`base_url` is accepted only for a host on your own machine or network — a
loopback or private address, a single-label name such as `ollama`, or a
`.local`/`.internal` name. A remote Ollama has to be set in `config.yaml`, see
[SECURITY.md](SECURITY.md#network).

## Setup steps

### 1. Pull the models and smoke test them

```bash
docker exec <ollama-container> ollama pull qwen2.5:14b
docker exec <ollama-container> ollama pull nomic-embed-text
docker exec <ollama-container> ollama list

curl http://localhost:11434/api/generate -d '{"model":"qwen2.5:14b","prompt":"Hello","stream":false}'
curl http://localhost:11434/api/embeddings -d '{"model":"nomic-embed-text","prompt":"test"}'
```

### 2. Re-sync to generate embeddings

Items already in SQLite have no embeddings in ChromaDB. A re-sync writes one per
item as it saves. At 0.5-2 seconds each on CPU, 500 to 2000 items takes **5 to 60
minutes**.

```bash
python3.11 -m src.cli update --source all
```

Confirm they landed. Expect a count close to your item count:

```bash
python3.11 -c "
from pathlib import Path
from src.storage.vector_db import VectorDB
print(VectorDB(Path('data/chroma_db')).collection.count())
"
```

### 3. Test from the CLI before the UI

```bash
python3.11 -m src.cli recommend --type video_game --count 3
```

That exercises the whole scoring pipeline including `SemanticSimilarityScorer`.
With `llm_reasoning_enabled` on it also calls the model for explanations, so the
first response takes 30-60 seconds on CPU while the model loads. Try chat once
this works.

## Troubleshooting

**Nothing reaches Ollama.** Check `ollama.base_url`. App on the host with Ollama
port mapped out of Docker: `http://localhost:11434`. Both containerized under the
bundled compose file: `http://ollama:11434`.

**The first response is slow.** Model loading, and it recurs after the model is
evicted. Later requests reuse it.

**Out of memory.** Close other applications, or drop to a lighter model such as
`llama3.1:8b`. Embeddings are already generated one item at a time.

**Chat is slow but recommendations are fine.** Point
`ollama.conversation_model` at a small model and leave `ollama.model` on the 14B.
Compact mode helps too, see
[MODEL_RECOMMENDATIONS.md](MODEL_RECOMMENDATIONS.md).
