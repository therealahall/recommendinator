# Ollama Model Recommendations

This guide helps you choose the best Ollama models for Recommendinator.

## Model Strategy

We recommend using **two different models**:

1. **Embedding Model**: Specialized model for generating embeddings
2. **Recommendation Model**: General-purpose model for text generation and reasoning

## Recommended Models

### For Embeddings (Required)

**Primary Recommendation: `nomic-embed-text`**
```bash
ollama pull nomic-embed-text
```

- **Size**: ~274 MB
- **Purpose**: Generate high-quality embeddings for semantic search
- **Why**: Optimized specifically for embeddings, much better than using general models
- **AMD Compatible**: ✅ Yes

**Alternative: `all-minilm`**
```bash
ollama pull all-minilm
```
- Smaller model, faster but potentially lower quality embeddings

### For Recommendations (Text Generation)

**Option 1: `mistral:7b`**
- **Size**: ~4.4 GB
- **Purpose**: Generate recommendations and reasoning
- **Why**: Good balance of quality and performance
- **AMD Compatible**: ✅ Yes
- **Best for**: General recommendations

**Option 2: `deepseek-r1:latest`**
- **Size**: ~4.7 GB
- **Purpose**: Advanced reasoning for recommendations
- **Why**: Better at understanding complex preferences and reasoning
- **AMD Compatible**: ✅ Yes
- **Best for**: More nuanced, detailed recommendations

**Option 3: `llama3.2:3b`** (If you want something smaller)
```bash
ollama pull llama3.2:3b
```
- **Size**: ~2.0 GB
- **Purpose**: Faster recommendations with lower resource usage
- **AMD Compatible**: ✅ Yes

## Installation

### Step 1: Install Embedding Model (Required)

```bash
ollama pull nomic-embed-text
```

### Step 2: Verify Installation

```bash
ollama list
```

You should see:
- `nomic-embed-text` (for embeddings)
- `mistral:7b` or `deepseek-r1:latest` (for recommendations)

### Step 3: Update Configuration

Edit `config/config.yaml`:

```yaml
ollama:
  base_url: "http://ollama:11434"
  model: "mistral:7b"  # or "deepseek-r1:latest"
  embedding_model: "nomic-embed-text"
```

## Model Comparison

| Model | Size | Use Case | Quality | Speed | AMD Support |
|-------|------|----------|---------|-------|-------------|
| nomic-embed-text | 274 MB | Embeddings | ⭐⭐⭐⭐⭐ | Fast | ✅ |
| mistral:7b | 4.4 GB | Recommendations | ⭐⭐⭐⭐ | Medium | ✅ |
| deepseek-r1:latest | 4.7 GB | Recommendations | ⭐⭐⭐⭐⭐ | Medium | ✅ |
| llama3.2:3b | 2.0 GB | Recommendations | ⭐⭐⭐ | Fast | ✅ |

## Small Hardware / Dual-Model Setup

If your GPU VRAM is limited (e.g., 2 GB AMD mobile GPU), a 7B+ model will fall back to CPU, causing ~200s time-to-first-token in chat. The solution: use a **small model for conversation** and keep the larger model for recommendations.

### Configuration

```yaml
ollama:
  model: "qwen2.5:14b"           # Larger model for recommendation reasoning
  conversation_model: "qwen2.5:3b"  # Smaller model for fast chat

conversation:
  llm:
    context_window_size: 4096     # Limit context for 3B model memory
  context:
    compact_mode: true            # Reduces prompt by ~60-70%
```

### What `compact_mode` Does

When enabled, compact mode:
- Uses a **condensed system prompt** (~800 tokens vs ~3,000) with a single few-shot example instead of 30+ rule bullets
- Reduces **context items** (5 items instead of 10-20) using compact formatting (no genres, reviews, or score breakdowns)
- Enables **pre-LLM intent detection** — common actions like "I finished X" or "rate X 4/5" are handled instantly without calling the LLM
- Skips tool descriptions in the prompt (tools are handled pre-LLM)

### Expected Performance

| Setup | TTFT (Chat) | Token Count |
|-------|-------------|-------------|
| 14B on CPU (default) | ~200s | ~6,000-8,000 |
| 3B + compact mode | ~10-20s | ~2,000-3,000 |

### Recommended 3B Models

| Model | Size | Chat Quality | Speed |
|-------|------|-------------|-------|
| qwen2.5:3b | ~2.0 GB | Good | Fast |
| llama3.2:3b | ~2.0 GB | Good | Fast |
| phi-3.5-mini | ~2.2 GB | Good | Fast |

Install with: `ollama pull qwen2.5:3b`

## Why Two Models?

1. **Embedding models** are optimized for creating vector representations
   - Better semantic understanding
   - More efficient for similarity search
   - Smaller and faster

2. **Text generation models** are optimized for reasoning and language
   - Better at understanding context
   - Can provide detailed explanations
   - Better at following complex instructions

## Testing Your Setup

After installing models, verify the LLM client can connect:

```python
from src.llm.client import OllamaClient

client = OllamaClient()
print("Available models:", client.list_available_models())
print("Embedding model available:", client.check_model_available("nomic-embed-text"))
```

## Troubleshooting

### Model Not Found

If you get "model not found" errors:
1. Verify Ollama is running: `ollama serve`
2. Check available models: `ollama list`
3. Pull the model: `ollama pull <model-name>`

### Slow Performance

- Use smaller models if speed is more important than quality
- Consider `llama3.2:3b` for faster recommendations
- Embeddings are typically fast with `nomic-embed-text`

### Out of Memory

- Use smaller models (3B instead of 7B)
- Process embeddings in smaller batches
- Close other applications using GPU/VRAM

## AMD-Specific Notes

All recommended models work well on AMD processors with:
- ROCm (AMD's GPU compute platform)
- CPU-only mode (slower but works)
- Ollama's automatic hardware detection

If you encounter issues:
1. Check Ollama logs: `journalctl -u ollama` (if running as service)
2. Verify GPU detection: Check Ollama startup messages
3. Use CPU mode if needed (automatic fallback)
