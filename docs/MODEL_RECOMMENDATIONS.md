# Ollama Model Recommendations

Recommendinator uses two Ollama models: an embedding model that turns items into
similarity vectors, and a text generation model that writes explanations and
chat. These are the general defaults. For picks tuned to higher output quality,
see [OLLAMA_SETUP_GUIDE.md](OLLAMA_SETUP_GUIDE.md).

## Embeddings

`nomic-embed-text` (~274 MB) is the default and the right answer for nearly
everyone. `all-minilm` is smaller and faster with lower quality vectors.

```bash
ollama pull nomic-embed-text
```

## Text generation

| Model | Size | Quality | Speed | Pick it for |
|-------|------|---------|-------|-------------|
| `mistral:7b` | 4.4 GB | Good | Medium | The default. Balanced. |
| `deepseek-r1:latest` | 4.7 GB | Better | Medium | More nuanced reasoning over preferences |
| `llama3.2:3b` | 2.0 GB | Fair | Fast | Low RAM, or fast iteration |

All of them run on AMD hardware, under ROCm or CPU only.

```bash
ollama pull mistral:7b
ollama list          # confirm both models are present
```

## Point the app at them

These are database-backed settings. Use the **Settings** page or the `settings`
CLI:

```bash
python3.11 -m src.cli settings set ollama.model mistral:7b
python3.11 -m src.cli settings set ollama.embedding_model nomic-embed-text
python3.11 -m src.cli settings set ollama.base_url http://ollama:11434
```

**Under Docker, name the same models in your compose environment.** The Ollama
sidecar reads `OLLAMA_MODEL`, `OLLAMA_EMBEDDING_MODEL` and
`OLLAMA_CONVERSATION_MODEL`, and cannot see these settings. Point the app at a
model the sidecar never pulled and every request to Ollama fails. See
[DOCKER.md](DOCKER.md).

## Small hardware: a second chat model

On a small GPU (2 GB VRAM, say) a 7B model falls back to CPU and chat takes
around 200 seconds to first token. Run a small model for chat and keep the large
one for recommendation reasoning:

```bash
python3.11 -m src.cli settings set ollama.model qwen2.5:14b              # reasoning
python3.11 -m src.cli settings set ollama.conversation_model qwen2.5:3b  # chat
python3.11 -m src.cli settings set conversation.llm.context_window_size 4096
python3.11 -m src.cli settings set conversation.context.compact_mode true
```

Leave `ollama.conversation_model` empty and chat uses `ollama.model`.

Compact mode swaps in a condensed system prompt (~800 tokens instead of ~3,000),
cuts context to 5 items formatted without genres, reviews or score breakdowns,
and answers common actions like "I finished X" before the LLM is called at all.

| Setup | Time to first token | Tokens |
|-------|---------------------|--------|
| 14B on CPU | ~200s | 6,000-8,000 |
| 3B plus compact mode | ~10-20s | 2,000-3,000 |

Good 3B chat models, all around 2 GB: `qwen2.5:3b`, `llama3.2:3b`,
`phi-3.5-mini`.

## Check the connection

```python
from src.llm.client import OllamaClient

client = OllamaClient(base_url="http://localhost:11434")
print(client.list_available_models())
print(client.check_model_available("nomic-embed-text"))
```

## Troubleshooting

**"Model not found".** Check Ollama is running, then `ollama list` for what is
there and `ollama pull <model>` for what is not. Under Docker the sidecar only
holds what `OLLAMA_MODEL` and its siblings named.

**Slow.** Drop to a smaller model, or turn on compact mode for chat.

**Out of memory.** Smaller model, and close whatever else is holding GPU memory.

**AMD.** Ollama detects ROCm itself and falls back to CPU. Its startup log says
which it chose, or `journalctl -u ollama` when it runs as a service.
