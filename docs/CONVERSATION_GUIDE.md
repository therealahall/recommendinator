# Conversation & Chat Guide

The Recommendinator includes a conversational AI chat interface that lets you interact with your library using natural language. You can ask for recommendations, mark items as completed, rate content, and build up a preference profile — all through conversation.

**This feature requires AI to be enabled.** It is entirely opt-in and the Recommendinator works fully without it.

## Prerequisites

1. **Ollama installed and running** — See [OLLAMA_SETUP_GUIDE.md](OLLAMA_SETUP_GUIDE.md)
2. **AI features enabled** in your config:
   ```yaml
   features:
     ai_enabled: true
   ```
3. **A conversation model configured** (optional — falls back to your main model):
   ```yaml
   ollama:
     model: "mistral:7b"              # Used for recommendations and chat (default)
     conversation_model: ""           # Set a separate model for chat if desired
   ```

## Configuration

The conversation system is configured under the `conversation:` section of your config file:

```yaml
conversation:
  enabled: true                        # Master toggle for chat
  max_history_messages: 50             # Messages kept in context
  memory_extraction_enabled: true      # Auto-extract preferences from chats
  profile_regeneration_interval: 24    # Hours between profile regeneration (0 to disable)

  llm:
    temperature: 0.7                   # Response creativity (0.0-1.0)
    max_tokens: 2000                   # Maximum response length
    # context_window_size: 4096        # Override for small models (see below)

  context:
    max_relevant_items: 10             # Items retrieved via semantic search
    max_unconsumed_items: 20           # Backlog items included in context
    include_algorithmic_recs: true     # Include scored recommendations in context
    compact_mode: false                # Enable for 3B parameter models
```

### Compact Mode (Small Models)

If you're running a 3B parameter model (e.g., `qwen2.5:3b`), enable compact mode to reduce prompt size by 60-70%:

```yaml
ollama:
  conversation_model: "qwen2.5:3b"

conversation:
  llm:
    context_window_size: 4096          # Limit context for small models
  context:
    compact_mode: true                 # Condensed prompts, fewer context items
```

Compact mode uses:
- A condensed system prompt with examples instead of detailed rules
- Fewer context items (5 completed, 5 backlog vs 10/20)
- Pre-LLM intent detection for simple actions (skips the LLM entirely for things like "I finished Book X")
- Single recommendation picks instead of full lists

## Using the Chat

Chat is available through both the **web interface** and the **CLI**. In the web UI, navigate to the Chat tab. From the command line, use the `chat` command group (see below).

### What You Can Do

**Ask for recommendations:**
- "What should I read next?"
- "Recommend a video game similar to Baldur's Gate 3"
- "I'm in the mood for a short sci-fi book"

**Mark items as completed:**
- "I finished watching Fallout Season 2"
- "I just read Project Hail Mary, 5 out of 5"
- "Completed The Witcher 3"

**Rate or update items:**
- "Rate Dune 4 out of 5"
- "I'd give Succession a 5"

**State preferences:**
- "I love steampunk settings"
- "I don't enjoy first-person shooters"
- "I prefer shorter books lately"

**Search your library:**
- "Do I have any Dragon Age games?"
- "What Sanderson books do I own?"

### How It Works

1. You send a message
2. The system assembles context from your library, memories, and preferences
3. Your message and context are sent to the local LLM
4. The LLM can call tools (mark completed, save memory, search, etc.)
5. The response streams back to you in real time

When the LLM updates your library (marking something completed, changing a rating), those changes are immediately reflected in your recommendations.

## Memories

Memories are persistent preference signals that carry across conversations. They come in two forms:

- **User-stated** — Things you explicitly tell the chat ("I love sci-fi", "avoid horror"). These have full confidence.
- **Inferred** — Preferences the system extracts from your conversation history. These have lower confidence scores.

### Managing Memories

In the web UI, the chat interface shows a **Memories** panel. You can also manage memories via the `memory` CLI commands (see [CLI Commands](#cli-commands) below). Both interfaces let you:
- View all active memories
- Add new memories manually
- Edit existing memories
- Delete memories that are no longer accurate

Memories directly influence the recommendations the chat gives you. If the system has an incorrect memory (e.g., "dislikes psychological thrillers" when you actually enjoy them), deleting or correcting that memory will improve future recommendations.

### Automatic Memory Extraction

When `memory_extraction_enabled: true`, the system runs a secondary LLM pass after conversations to extract preferences. For example, if you say "I've been really into strategy games lately," the system may save a memory noting your current interest in strategy games.

## User Profiles

The profile system analyzes your completed and rated items to build a preference summary. This summary is included in the LLM's context so it understands your tastes.

**What the profile captures:**
- **Genre affinities** — Genres you rate highly (requires 2+ rated items per genre)
- **Theme preferences** — Keywords from your highly-rated items (4+ stars)
- **Anti-preferences** — Genres where your average rating is low
- **Cross-media patterns** — e.g., "Loves sci-fi in books but prefers fantasy in games"

**Profile regeneration** happens automatically on a configurable interval (default: every 24 hours). You can also manually regenerate your profile from the web UI if you've added a lot of new ratings and want the profile to update immediately.

The profile is not always perfect — it's derived from your data and may occasionally mischaracterize your preferences (e.g., reporting you dislike a genre when you actually just haven't rated enough items in it). As you rate more content, the profile becomes more accurate.

## CLI Commands

The `chat` and `memory` CLI command groups provide terminal-based alternatives to the web UI.

### Chat Commands

```bash
# Start an interactive REPL session
python3.11 -m src.cli chat start

# Filter to a specific content type
python3.11 -m src.cli chat start --type book

# Send a single message without entering the REPL
python3.11 -m src.cli chat send --message "Recommend a sci-fi book"

# View recent conversation history
python3.11 -m src.cli chat history --limit 10

# Clear conversation history
python3.11 -m src.cli chat reset
```

### Memory Commands

```bash
# List all memories
python3.11 -m src.cli memory list

# Add a memory manually
python3.11 -m src.cli memory add --text "I love hard sci-fi"

# Edit a memory's text and/or active state (matches web API PUT /api/memories/{id})
python3.11 -m src.cli memory edit --id 3 --text "I love hard sci-fi and space opera"
python3.11 -m src.cli memory edit --id 3 --inactive
python3.11 -m src.cli memory edit --id 3 --text "..." --inactive

# Flip a memory's active state (convenience shortcut)
python3.11 -m src.cli memory toggle --id 3

# Delete a memory
python3.11 -m src.cli memory delete --id 3
```

## Troubleshooting

### Chat returns "LLM not configured"

Ensure all of these are set:
- `features.ai_enabled: true`
- `conversation.enabled: true`
- Ollama is running and accessible at the configured `ollama.base_url`
- At least one model is available (`ollama.model` or `ollama.conversation_model`)

### Chat is slow

- You might be CPU bound as models rely on GPU memory to be effective
- Use a smaller model for chat: set `ollama.conversation_model` to a faster model (e.g., `qwen2.5:3b`)
- Enable `compact_mode` to reduce prompt size
- Set `context_window_size` to limit context for small models
- Reduce `max_relevant_items` and `max_unconsumed_items` to minimize context assembly time

### Recommendations in chat don't match web recommendations

Chat recommendations include additional context from your conversation history and memories, so they may differ from the pure algorithmic recommendations on the Recommendations page. This is expected — the chat has more context about your current mood and recent preferences.

### Profile seems inaccurate

The profile is data-driven. If it says you dislike a genre you actually enjoy, it may be because:
- You haven't rated enough items in that genre (minimum 2 items required)
- Your ratings in that genre happen to be lower than average
- Regenerate the profile after adding more ratings to improve accuracy